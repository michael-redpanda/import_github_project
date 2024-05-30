# Github Issue Importer

This script will create JIRA issues from Github issues.

## Prerequisites

### Clone this repo

```commandline
git clone https://github.com/michael-redpanda/import_github_project
```

### JIRA Token

Create a JIRA token following the instructions
found [here](https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/)

### `gh` cli

Install the `gh` CLI and login using `gh auth login`

### Python environment

```bash
cd import_github_project
python3 -m venv venv
. ./venv/bin/activate
pip install -r requirements.txt
```

### Mapping File

Not saved with this repo, but you will need a CSV file that maps
Redpanda Github users to their Redpanda emails. The CSV file
should have the following header:

```csv
Github Username,Name,Email
```

### `pandoc` application

This script now supports converting GH markdown to Jira markdown by
using `pandoc`. Install pandoc on your system. For MacOS use:

```commandline
brew install pandoc
```

## Running Application

```bash
python app.py --help

usage: Github Issue Importer [-h] [-v] -g GITHUB_REPO [-l LIMIT] -u JIRA_USER -t JIRA_TOKEN [-j JIRA_URL] -p JIRA_PROJECT

Imports issues from github

optional arguments:
  -h, --help            show this help message and exit
  -v, --verbose         Be more verbose
  -g GITHUB_REPO, --github-repo GITHUB_REPO
                        Github Repo to access
  -l LIMIT, --limit LIMIT
                        limit to number of issues to fetch (default: 100000)
  -u JIRA_USER, --jira-user JIRA_USER
                        Jira User
  -t JIRA_TOKEN, --jira-token JIRA_TOKEN
                        Jira Token
  -j JIRA_URL, --jira-url JIRA_URL
                        URL to JIRA project (default: https://redpandadata.atlassian.net
  -p JIRA_PROJECT, --jira-project JIRA_PROJECT
                        Jira project to import into
```

* `GITHUB_REPO` is the Github repo that will be queried by `gh`.
* `LIMIT` is the limit of how many issues to query in Github
* `JIRA_USER` Your JIRA username
* `JIRA_TOKEN` the JIRA token to use
* `JIRA_URL` The URL of the JIRA instance
* `JIRA_PROJECT` the project within the instance to add issues to

