"""Tests for personalized learning paths (Phase 4)."""

from analyzer.file_scanner import analyze_source
from analyzer.github_client import RepoMetadata
from analyzer.issue_client import IssueInfo
from analyzer.issue_linker import IssueLinkResult
from analyzer.learning_path import build_learning_path
from analyzer.recommender import ContributorProfile

FLASK_APP = "import flask\n\ndef create_app():\n    return flask.Flask(__name__)\n"
TEST_FILE = "def test_something():\n    assert True\n"


def make_issue(title="Fix a bug", body="It crashes.", labels=None):
    return IssueInfo(
        number=7,
        title=title,
        body=body,
        labels=list(labels or []),
        html_url="https://github.com/acme/demo/issues/7",
        repo_full_name="acme/demo",
    )


def make_meta():
    return RepoMetadata(
        owner="acme",
        name="demo",
        full_name="acme/demo",
        default_branch="main",
        commit_sha="abc123",
        html_url="https://github.com/acme/demo",
        language="Python",
    )


def make_modules():
    return {
        "app.py": analyze_source("app.py", FLASK_APP),
        "tests/test_app.py": analyze_source("tests/test_app.py", TEST_FILE),
    }


def empty_link():
    return IssueLinkResult(repo_full_name="acme/demo", issue_number=7)


def test_topics_are_ordered_from_one():
    path = build_learning_path(
        make_issue(), ContributorProfile(), empty_link(), make_meta(), make_modules()
    )
    orders = [t.order for t in path.topics]
    assert orders == list(range(1, len(path.topics) + 1))


def test_python_fundamentals_are_first():
    path = build_learning_path(
        make_issue(), None, empty_link(), make_meta(), make_modules()
    )
    assert "Python" in path.topics[0].title


def test_declared_skill_marks_topic_reinforce():
    profile = ContributorProfile(skills=["python"])
    path = build_learning_path(
        make_issue(), profile, empty_link(), make_meta(), make_modules()
    )
    assert path.topics[0].status == "reinforce"
    assert "python" in path.topics[0].matched_skills


def test_no_profile_all_topics_to_learn():
    path = build_learning_path(
        make_issue(), None, empty_link(), make_meta(), make_modules()
    )
    assert all(t.status == "to_learn" for t in path.topics)


def test_bug_issue_adds_reproduction_topic():
    path = build_learning_path(
        make_issue(title="Crash on startup", labels=["bug"]),
        ContributorProfile(),
        empty_link(),
        make_meta(),
        make_modules(),
    )
    titles = " ".join(t.title.lower() for t in path.topics)
    assert "reproducing" in titles


def test_detected_framework_adds_topic_with_canonical_link():
    path = build_learning_path(
        make_issue(), ContributorProfile(), empty_link(), make_meta(), make_modules()
    )
    flask_topics = [t for t in path.topics if "Flask" in t.title]
    assert flask_topics
    assert any("flask.palletsprojects.com" in r for r in flask_topics[0].resources)


def test_tests_present_adds_testing_topic():
    path = build_learning_path(
        make_issue(), ContributorProfile(), empty_link(), make_meta(), make_modules()
    )
    testing = [t for t in path.topics if "tests" in t.title.lower()]
    assert testing
    assert any("docs.pytest.org" in r for r in testing[0].resources)


def test_git_workflow_topic_is_last():
    path = build_learning_path(
        make_issue(), ContributorProfile(), empty_link(), make_meta(), make_modules()
    )
    assert "pull request" in path.topics[-1].title.lower()
    assert any("git-scm.com" in r for r in path.topics[-1].resources)


def test_all_resources_are_canonical_https_links():
    path = build_learning_path(
        make_issue(), ContributorProfile(), empty_link(), make_meta(), make_modules()
    )
    for topic in path.topics:
        for resource in topic.resources:
            assert resource.startswith("https://")


def test_profile_notes_state_skill_is_not_assumed():
    path = build_learning_path(
        make_issue(),
        ContributorProfile(skills=["python"]),
        empty_link(),
        make_meta(),
        make_modules(),
    )
    joined = " ".join(path.profile_notes).lower()
    assert "is not treated as evidence" in joined


def test_relevant_file_becomes_topic_where():
    from analyzer.issue_linker import LinkedFile

    link = IssueLinkResult(
        repo_full_name="acme/demo",
        issue_number=7,
        linked_files=[
            LinkedFile(
                path="app.py",
                module_name="app",
                score=60.0,
                confidence="confirmed",
                symbols=["function create_app"],
            )
        ],
    )
    path = build_learning_path(
        make_issue(), ContributorProfile(), link, make_meta(), make_modules()
    )
    file_topics = [t for t in path.topics if "app.py" in t.title]
    assert file_topics
    assert "app.py" in file_topics[0].where


def test_learning_path_to_dict_serialisable():
    import json

    path = build_learning_path(
        make_issue(), ContributorProfile(skills=["python"]), empty_link(), make_meta(), make_modules()
    )
    json.dumps(path.to_dict())
