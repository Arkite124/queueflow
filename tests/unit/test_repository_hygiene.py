from scripts.check_repository_hygiene import issues


def test_machine_paths_and_hostnames_are_rejected_without_echoing_values():
    for value in (("C:" + "/Users/" + "example/project").encode(),
                  ("C:" + "\\\\Users\\\\" + "example\\\\project").encode(),
                  ("D:" + "/private/project").encode(),
                  ("/home/" + "example/project").encode(), ("DESKTOP-" + "EXAMPLE").encode()):
        result = issues("docs/example.md", value)
        assert result == ["personal absolute path or machine hostname found"]


def test_forced_add_of_local_records_is_rejected():
    for path in ("docs/validation-results/results.json", "docs/SERVER_DEPLOYMENT_STATUS.md", "output/report.xlsx",
                 "docs/API_DEPLOYMENT_example.md", "logs/runtime.log", ".env"):
        assert issues(path, b"example")


def test_portable_instructions_and_standard_install_locations_are_allowed():
    for value in (b"python -m pytest", b"$env:ProgramFiles", b"C:/Program Files/Docker/Docker", b"output/sample.csv",
                  b"desktop-linux"):
        assert issues("docs/TESTING.md", value) == []
    assert issues(".env.example", b"AUTH_MODE=local") == []
