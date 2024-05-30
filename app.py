#!/usr/bin/env python3
import argparse
import csv
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from typing import List, Optional

from atlassian import Jira

DEFAULT_API_VER = 2
LIMIT_DEFAULT = 100000
JIRA_PROJECT_DEFAULT = "https://redpandadata.atlassian.net"
API_BASE = '{url}/rest/api/{api_version}'
CSV_GITHUB_USERNAME = 'Github Username'
CSV_NAME = 'Name'
CSV_EMAIL = 'Email'
EXPECTED_FIELD_NAMES = [CSV_GITHUB_USERNAME, CSV_NAME, CSV_EMAIL]
JIRA_ISSUE_CHARACTER_LIMIT = 32767


class NoUserExists(Exception):

    def __init__(self, email: str):
        super().__init__(f'No Jira user exists with email {email}')
        self._email = email

    @property
    def email(self):
        return self._email


class GithubIssueImport(object):
    _issue_list_pattern = 'gh issue list -R {repo} --json title,labels,url,body,comments,number,author,assignees -L {limit}'
    _null_panda_email = 'noreply@redpanda.com'

    def __init__(self,
                 logger: logging.Logger,
                 github_repo: str,
                 limit: int,
                 jira_user: str,
                 jira_token: str,
                 jira_url: str,
                 jira_project: str,
                 user_mapper: csv.DictReader,
                 pandoc: Optional[str],
                 add_link: bool = True):
        self._logger = logger
        self._github_repo = github_repo
        self._limit = limit
        self._jira_user = jira_user
        self._jira_token = jira_token
        self._jira_url = jira_url
        self._jira_project = jira_project
        self._pandoc = pandoc
        self._add_link = add_link
        self._jira = Jira(url=self._jira_url,
                          username=self._jira_user,
                          password=self._jira_token,
                          cloud=True)
        self._null_panda_user = self._get_jira_user(self._null_panda_email)
        # Holds mapping of Github user name to the Jira user
        # If the Github user does not exist in Jira, NullPanda is used instead
        self._mapped_users = self._create_user_mapping(user_mapper,
                                                       self._null_panda_user)

    def _create_issue(self,
                      description: str,
                      summary: str,
                      issue_type: str,
                      labels: [str],
                      project_key: str,
                      issue_url: str,
                      assignee: str = None):
        fields = {
            "description": description,
            "summary": summary,
            "issuetype": {
                "name": issue_type
            },
            "labels": labels,
            "project": {
                "key": project_key
            },
            "customfield_10052": issue_url
        }

        if assignee is not None:
            fields["assignee"] = {"id": assignee}

        return self._jira.issue_create(fields=fields)

    def _create_user_mapping(self, user_mapper: csv.DictReader,
                             default_user: str):
        rv = {}
        for row in user_mapper:
            rv[row[CSV_GITHUB_USERNAME]] = self._get_jira_user_with_default(
                row[CSV_EMAIL], default_user)
        return rv

    def _ghm_to_jira(self, ghm: str):
        if self._pandoc is not None:
            with tempfile.NamedTemporaryFile(delete=False) as tf:
                tf.write(ghm.encode())
                tf.flush()
                tf.close()
                jmd = self._run_cmd_return_stdout(
                    f'{self._pandoc} -f gfm -w jira {tf.name}')
                os.unlink(tf.name)
                return jmd
        return ghm

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
        comment = self._ghm_to_jira(comment)
        self._logger.debug(f'Adding comment "{comment}" to issue {issue_id}')
        self._jira.issue_add_comment(issue_key=issue_id, comment=comment)

    def _collect_issues(self) -> List:
        return json.loads(
            self._run_cmd_return_stdout(
                self._issue_list_pattern.format(repo=self._github_repo,
                                                limit=self._limit)))

    def _get_jira_user(self, email: str) -> str:
        self._logger.debug(f'Querying JIRA for user with email {email}')
        resp = self._jira.user_find_by_user_string(query=email)
        if len(resp) == 0:
            raise NoUserExists(email=email)

        self._logger.debug(
            f'Jira user with email {email}: {resp[0]["accountId"]}')
        return resp[0]["accountId"]

    def _get_jira_user_with_default(self, email: str,
                                    default_user: str) -> str:
        try:
            return self._get_jira_user(email)
        except NoUserExists:
            return default_user

    def _import_issues(self, issues: List):
        self._logger.debug(
            f'Starting to import {len(issues)} issues into project {self._jira_project} at {self._jira_url}'
        )

        for issue in reversed(issues):
            self._logger.debug(
                f"Checking to see if a JIRA issue exists that's linked to '{issue['url']}'"
            )
            issue_exists = self._jira_issue_linked_to_gh_issue(issue['url'])
            if issue_exists:
                self._logger.info(f'Skipping issue {issue["number"]}')
                continue
            labels = [
                label['name'].replace(" ", "-") for label in issue["labels"]
            ]
            issue_type = "Bug" if 'kind/bug' in labels else "Task"
            assignee = None
            if len(issue["assignees"]) > 0:
                assignee = self._mapped_users.get(
                    issue["assignees"][0]["login"], self._null_panda_user)

            issue_body = self._ghm_to_jira(issue["body"])

            issue_cut_off = len(issue_body) > JIRA_ISSUE_CHARACTER_LIMIT

            self._logger.debug(
                f'Creating issue of type {issue_type} titled "{issue["title"]}" with labels "{labels}"'
            )
            response = self._create_issue(
                issue_body[:JIRA_ISSUE_CHARACTER_LIMIT], issue["title"],
                issue_type, labels, self._jira_project, issue["url"], assignee)

            if assignee is not None and response is None:
                self._logger.debug(
                    f'Resubmitting creation of issue with no assignee due to error'
                )
                response = self._create_issue(
                    issue_body[:JIRA_ISSUE_CHARACTER_LIMIT], issue["title"],
                    issue_type, labels, self._jira_project, issue["url"], None)

            if response is None:
                raise RuntimeError("Failed to create issue")

            issue_key = response['key']
            issue_id = response['id']
            message = """
JIRA Issue created from GitHub issue.  Any updates in JIRA will _not_ be pushed back
to the GitHub issue.  New comments from GitHub will sync with JIRA issue, but not
modifications.  Please refer to the External GitHub Link field to get to the GitHub
issue that triggered this issue's creation.
            """
            if issue_cut_off:
                message += """
The issue has been truncated due to issue length limitations.
Please refer to the original Github issue for the full issue body.
                """
            self._logger.debug(
                f'Adding boilerplate message to issue {issue_key}')
            self._add_comment_to_issue(issue_id, message)

            # The backport issues that were autocreated lack the trailing ``` and so the link shows up weird
            # within the code block so don't insert the JIRA link for kind/backports
            insert_jira_link = 'kind/backport' not in labels and self._add_link

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

            for c in reversed(issue["comments"]):
                self._add_comment_to_issue(issue_id, c['body'])

            self._logger.info(f'Successfully created JIRA Issue {issue_key}')

    def _jira_issue_linked_to_gh_issue(self, gh_url) -> bool:
        jql_request = f'project = {self._jira_project} and "External GitHub Issue[URL Field]" = "{gh_url}"'
        resp = self._jira.jql(jql=jql_request, fields='summary')
        self._logger.debug(
            f'Submitting request to find JIRA issue with GitHub link "{gh_url}"'
        )
        total_issues: int = resp["total"]
        self._logger.debug(f'Found {total_issues} issues linked to "{gh_url}"')

        return total_issues != 0

    def _run_cmd_return_stdout(self, cmd: str) -> str:
        self._logger.debug(f'Executing command "{cmd}"')
        return subprocess.check_output(cmd.split(' ')).decode()


def main() -> int:
    args = parse_args()
    reader = csv.DictReader(args.user_mapping)
    assert reader.fieldnames == EXPECTED_FIELD_NAMES, f'Invalid field names.  Expected {EXPECTED_FIELD_NAMES} but got {reader.fieldnames}'
    logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
    pandoc: Optional[
        str] = args.pandoc if args.pandoc is not None else find_prog('pandoc')
    issue_importer = GithubIssueImport(logger,
                                       args.github_repo,
                                       args.limit,
                                       args.jira_user,
                                       args.jira_token,
                                       args.jira_url,
                                       args.jira_project,
                                       reader,
                                       pandoc=pandoc,
                                       add_link=not args.dont_add_link)
    try:
        issue_importer.run()
    except RuntimeError as e:
        logger.error(f'Failed executing issue importer: {e}')
    return 0


def find_prog(prog) -> Optional[str]:
    return shutil.which(prog)


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
    parser.add_argument(
        '-m',
        '--user-mapping',
        help=
        'Path to the CSV file containing mapping of github user with redpanda email address',
        required=True,
        type=argparse.FileType('r'))
    parser.add_argument('--pandoc', help='Path to pandoc executable')
    parser.add_argument('--dont-add-link',
                        help='Set this to not add the link',
                        action='store_true')
    return parser.parse_args()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        logging.error(f'Failed execution of application: {e}')
        sys.exit(1)
