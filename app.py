#!/usr/bin/env python3

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
from typing import List

import requests
from requests.auth import HTTPBasicAuth

DEFAULT_API_VER = 2
LIMIT_DEFAULT = 100000
JIRA_PROJECT_DEFAULT = "https://redpandadata.atlassian.net"
API_BASE = '{url}/rest/api/{api_version}'


class GithubIssueImport(object):
    _issue_list_pattern = 'gh issue list -R {repo} --json title,labels,url,body,comments,number -L {limit}'
    _post_headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json'
    }
    _get_headers = {'Accept': 'application/json'}
    _api_base = '{url}/rest/api/{api_version}/'
    _api_version = 2

    def __init__(self, logger: logging.Logger, github_repo: str, limit: int,
                 jira_user: str, jira_token: str, jira_url: str,
                 jira_project: str):
        self._logger = logger
        self._github_repo = github_repo
        self._limit = limit
        self._jira_user = jira_user
        self._jira_token = jira_token
        self._jira_url = jira_url
        self._jira_project = jira_project

    def run(self):
        self._logger.info(
            f'Starting run, collecting Github Issues from {self._github_repo} and importing into project {self._jira_project} at {self._jira_url}'
        )
        self._logger.debug(f'Fetching open issues from {self._github_repo}')
        issues = self._collect_issues()
        self._logger.debug(
            f'There are {len(issues)} issues open in {self._github_repo}')
        self._logger.debug('Starting import process')
        self._import_issues(issues)

    def _add_comment_to_issue(self, issue_id, comment):
        self._logger.debug(f'Adding comment "{comment}" to issue {issue_id}')
        payload = json.dumps({"body": comment})
        self._submit_jira_api_request(method="POST",
                                      endpoint=f'issue/{issue_id}/comment',
                                      headers=self._post_headers,
                                      data=payload)

    def _collect_issues(self) -> List:
        return json.loads(
            self._run_cmd_return_stdout(
                self._issue_list_pattern.format(repo=self._github_repo,
                                                limit=self._limit)))

    def _form_url(self, endpoint: str) -> str:
        return self._api_base.format(url=self._jira_url,
                                     api_version=self._api_version) + endpoint

    def _get_auth(self) -> HTTPBasicAuth:
        return HTTPBasicAuth(username=self._jira_user,
                             password=self._jira_token)

    def _import_issues(self, issues: List):
        self._logger.debug(
            f'Starting to import {len(issues)} issues into project {self._jira_project} at {self._jira_url}'
        )

        for issue in issues:
            self._logger.debug(
                f"Checking to see if a JIRA issue exists that's linked to '{issue['url']}'"
            )
            issue_exists = self._jira_issue_linked_to_gh_issue(issue['url'])
            if issue_exists:
                self._logger.info(f'Skipping issue {issue["number"]}')
                continue
            labels = [label['name'] for label in issue["labels"]]
            issue_type = "Bug" if 'kind/bug' in labels else "Task"

            self._logger.debug(
                f'Creating issue of type {issue_type} titled "{issue["title"]}" with labels "{labels}"'
            )
            payload = json.dumps({
                "fields": {
                    "description": issue["body"],
                    "summary": issue["title"],
                    "issuetype": {
                        "name": issue_type
                    },
                    "labels": labels,
                    "project": {
                        "key": self._jira_project
                    },
                    "customfield_10052": issue["url"]
                }
            })
            response = self._submit_jira_api_request(
                method="POST",
                endpoint="issue",
                data=payload,
                headers=self._post_headers)
            response = json.loads(response.text)
            issue_key = response['key']
            issue_id = response['id']
            message = """
            JIRA Issue created from GitHub issue.  Any updates in JIRA will _not_ be pushed back
            to the GitHub issue.  New comments from GitHub will sync with JIRA issue, but not
            modifications.  Please refer to the External GitHub Link field to get to the GitHub
            issue that triggered this issue's creation.
            """
            self._logger.debug(
                f'Adding boilerplate message to issue {issue_key}')
            self._add_comment_to_issue(issue_id, message)

            # The backport issues that were autocreated lack the trailing ``` and so the link shows up weird
            # within the code block so don't insert the JIRA link for kind/backports
            insert_jira_link = 'kind/backport' not in labels

            if insert_jira_link:
                jira_issue_url = f'{self._jira_url}/browse/{issue_key}'
                issue_body = issue[
                    "body"] + f"\n\nJIRA Link: [{issue_key}]({jira_issue_url})"
                with tempfile.NamedTemporaryFile(delete=False) as tf:
                    tf.write(issue_body.encode())
                    tf.flush()
                    tf.close()
                    self._run_cmd_return_stdout(
                        f"gh issue edit {issue['url']} --body-file {tf.name}")
                    os.unlink(tf.name)

            for c in issue["comments"]:
                self._add_comment_to_issue(issue_id, c['body'])

            self._logger.info(f'Successfully created JIRA Issue {issue_key}')

    def _jira_issue_linked_to_gh_issue(self, gh_url) -> bool:
        query = {
            'jql':
            f'project = {self._jira_project} and "External GitHub Issue[URL Field]" = "{gh_url}"',
            'fields': 'summary'
        }
        self._logger.debug(
            f'Submitting request to find JIRA issue with GitHub link "{gh_url}"'
        )

        resp = json.loads(
            self._submit_jira_api_request(method='GET',
                                          endpoint='search',
                                          params=query,
                                          headers=self._get_headers).text)
        total_issues: int = resp["total"]
        self._logger.debug(f'Found {total_issues} issues linked to "{gh_url}"')

        return total_issues != 0

    def _run_cmd_return_stdout(self, cmd: str) -> str:
        self._logger.debug(f'Executing command "{cmd}"')
        return subprocess.check_output(cmd.split(' ')).decode()

    def _submit_jira_api_request(self, method, endpoint,
                                 **kwargs) -> requests.Response:
        url = self._form_url(endpoint)
        log_message = f'Sending {method} to {url}'

        if 'data' in kwargs:
            log_message += f' containing data {kwargs["data"]}'

        if 'parameters' in kwargs:
            log_message += f' with parameters {kwargs["parameters"]}'

        self._logger.debug(log_message)

        resp = requests.request(method=method,
                                url=url,
                                auth=self._get_auth(),
                                **kwargs)
        self._logger.debug(f'Response: {resp.text}')
        return resp


def main() -> int:
    args = parse_args()
    logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
    issue_importer = GithubIssueImport(logger, args.github_repo, args.limit,
                                       args.jira_user, args.jira_token,
                                       args.jira_url, args.jira_project)
    try:
        issue_importer.run()
    except RuntimeError as e:
        logger.error(f'Failed executing issue importer: {e}')
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog='Github Issue Importer',
                                     description='Imports issues from github')
    parser.add_argument('-v',
                        '--verbose',
                        action='store_true',
                        help="Be more verbose")
    parser.add_argument('-g',
                        '--github-repo',
                        help='Github Repo to access',
                        required=True)
    parser.add_argument(
        '-l',
        '--limit',
        help=f"limit to number of issues to fetch (default: {LIMIT_DEFAULT})",
        default=LIMIT_DEFAULT)
    parser.add_argument('-u', '--jira-user', help="Jira User", required=True)
    parser.add_argument('-t', '--jira-token', help="Jira Token", required=True)
    parser.add_argument(
        '-j',
        '--jira-url',
        help=f'URL to JIRA project (default: {JIRA_PROJECT_DEFAULT}',
        default=JIRA_PROJECT_DEFAULT)
    parser.add_argument('-p',
                        '--jira-project',
                        help="Jira project to import into",
                        required=True)
    return parser.parse_args()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        logging.error(f'Failed execution of application: {e}')
        sys.exit(1)
