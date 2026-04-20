{
  self,
  pkgs,
  system,
  yamtrack,
  yamtrackDeps,
  python,
}:
let
  baseTestDeps =
    ps:
    yamtrackDeps
    ++ [
      ps.pytest
      ps.pytest-django
      ps.fakeredis
      ps.lupa
      ps.tblib
    ];

  testPython = python.withPackages baseTestDeps;

  playwrightTestPython = python.withPackages (
    ps:
    baseTestDeps ps
    ++ [
      ps.playwright
      ps.pytest-playwright
      ps.pytest-rerunfailures
      ps.pytest-timeout
    ]
  );
in
{
  yamtrack-unit-tests = pkgs.runCommand "yamtrack-unit-tests"
    {
      nativeBuildInputs = [ testPython ];
    }
    ''
      cp -r ${yamtrack}/lib/yamtrack /tmp/yamtrack-test
      chmod -R u+w /tmp/yamtrack-test
      cd /tmp/yamtrack-test
      # inject conftest that mocks all external API calls
      cp ${./conftest.py} conftest.py
      export DJANGO_SETTINGS_MODULE=config.test_settings
      export HOME=/tmp
      ${testPython.interpreter} -m pytest \
        --ignore=app/tests/test_integration.py \
        --ignore=lists/tests/test_integration.py \
        --ignore=app/tests/providers/test_metadata.py \
        --ignore=app/tests/providers/test_search.py \
        --ignore=integrations/tests/imports/test_anilist.py \
        --ignore=integrations/tests/imports/test_goodreads.py \
        --ignore=integrations/tests/imports/test_hltb.py \
        --ignore=integrations/tests/imports/test_imdb.py \
        --ignore=integrations/tests/imports/test_mal.py \
        --ignore=integrations/tests/imports/test_simkl.py \
        --ignore=integrations/tests/imports/test_yamtrack.py \
        --ignore=integrations/tests/test_webhooks_emby.py \
        --ignore=integrations/tests/test_webhooks_jellyfin.py \
        --ignore=integrations/tests/test_webhooks_plex.py \
        --deselect=app/tests/views/test_entry.py::CreateEntryViewTests::test_create_entry_post_movie \
        -x
      touch $out
    '';

  yamtrack-sqlite = pkgs.testers.nixosTest {
    name = "yamtrack-sqlite";
    nodes.machine =
      { ... }:
      {
        imports = [ self.nixosModules.default ];
        environment.systemPackages = [ self.packages.${system}.default ];
        services.yamtrack = {
          enable = true;
          package = self.packages.${system}.default;
          hostName = "localhost";
        };
      };
    testScript = ''
      import json

      base_url = "http://localhost:8001"

      machine.wait_for_unit("yamtrack.service")
      machine.wait_for_unit("yamtrack-celery-worker.service")
      machine.wait_until_succeeds(f"curl -fs {base_url}/accounts/login/", timeout=60)

      # Check health endpoint returns success with JSON details
      machine.wait_until_succeeds(f"curl -fs {base_url}/health/", timeout=120)
      health = machine.succeed(f"curl -s {base_url}/health/?format=json")
      health_data = json.loads(health)
      for check, status in health_data.items():
          assert status == "working" or status == "OK", f"Health check '{check}' failed: {status}"

      # Create a test user via the yamtrack service environment
      manage = "sudo -u yamtrack env DJANGO_SETTINGS_MODULE=config.settings PYTHONPATH=${self.packages.${system}.default}/lib/yamtrack DB_PATH=/var/lib/yamtrack/db/db.sqlite3 yamtrack-manage"
      machine.succeed(f"{manage} createsuperuser --noinput --username testuser --email test@test.com")
      machine.succeed(f"""{manage} shell -c "
          from django.contrib.auth import get_user_model;
          u = get_user_model().objects.get(username='testuser');
          u.set_password('testpass123'); u.save()
      " """)

      # Log in: get login page and extract CSRF token, then POST credentials
      machine.succeed(f"curl -s -c /tmp/cookies.txt {base_url}/accounts/login/ > /tmp/login.html")
      csrf_token = machine.succeed(
          "grep -oP 'csrfmiddlewaretoken.*?value=\"\\K[^\"]+' /tmp/login.html"
      ).strip()
      login_response = machine.succeed(f"""
          curl -s -b /tmp/cookies.txt -c /tmp/cookies.txt -w '\n%{{http_code}}'
          -H 'X-Real-IP: 127.0.0.1'
          -H 'Origin: {base_url}' -H 'Referer: {base_url}/accounts/login/'
          -d 'csrfmiddlewaretoken={csrf_token}&login=testuser&password=testpass123'
          {base_url}/accounts/login/
      """)
      # Successful login returns 302 redirect
      assert "302" in login_response, f"Login failed: {login_response[-200:]}"

      # Verify we are logged in (home page doesn't redirect to login)
      home_status = machine.succeed(f"""
          curl -s -o /dev/null -w '%{{http_code}}'
          -b /tmp/cookies.txt -c /tmp/cookies.txt {base_url}/
      """).strip()
      assert home_status == "200", f"Not logged in, got status: {home_status}"

      # Create a game entry via the manual create form
      machine.succeed(f"curl -s -b /tmp/cookies.txt -c /tmp/cookies.txt {base_url}/create > /tmp/create.html")
      csrf_token = machine.succeed(
          "grep -oP 'csrfmiddlewaretoken.*?value=\"\\K[^\"]+' /tmp/create.html | head -1"
      ).strip()
      create_response = machine.succeed(f"""
          curl -s -b /tmp/cookies.txt -c /tmp/cookies.txt -w '\n%{{http_code}}'
          -H 'Referer: {base_url}/create'
          -d 'csrfmiddlewaretoken={csrf_token}&media_type=game&title=Test+Game+Entry&status=Planning&score=&progress='
          {base_url}/create
      """)
      # Successful creation returns 302 redirect
      assert "302" in create_response, f"Create entry failed: {create_response[-500:]}"

      # Verify the game appears in the games list
      machine.succeed(f"""
          curl -s -b /tmp/cookies.txt -c /tmp/cookies.txt {base_url}/medialist/game
          | grep -q 'Test Game Entry'
      """)
    '';
  };

  yamtrack-postgresql = pkgs.testers.nixosTest {
    name = "yamtrack-postgresql";
    nodes.machine =
      { ... }:
      {
        imports = [ self.nixosModules.default ];
        environment.systemPackages = [ self.packages.${system}.default ];
        services.yamtrack = {
          enable = true;
          package = self.packages.${system}.default;
          database.createLocally = true;
          hostName = "localhost";
        };
      };
    testScript = ''
      import json

      base_url = "http://localhost:8001"

      machine.wait_for_unit("postgresql.service")
      machine.wait_for_unit("yamtrack.service")
      machine.wait_for_unit("yamtrack-celery-worker.service")
      machine.wait_until_succeeds(f"curl -fs {base_url}/accounts/login/", timeout=60)

      # Check health endpoint returns success with JSON details
      machine.wait_until_succeeds(f"curl -fs {base_url}/health/", timeout=120)
      health = machine.succeed(f"curl -s {base_url}/health/?format=json")
      health_data = json.loads(health)
      for check, status in health_data.items():
          assert status == "working" or status == "OK", f"Health check '{check}' failed: {status}"

      # Create a test user via the yamtrack service environment
      manage = "sudo -u yamtrack env DJANGO_SETTINGS_MODULE=config.settings PYTHONPATH=${self.packages.${system}.default}/lib/yamtrack DB_HOST=/run/postgresql DB_NAME=yamtrack DB_USER=yamtrack DB_PASSWORD= DB_PORT=5432 yamtrack-manage"
      machine.succeed(f"{manage} createsuperuser --noinput --username testuser --email test@test.com")
      machine.succeed(f"""{manage} shell -c "
          from django.contrib.auth import get_user_model;
          u = get_user_model().objects.get(username='testuser');
          u.set_password('testpass123'); u.save()
      " """)

      # Log in: get login page and extract CSRF token, then POST credentials
      machine.succeed(f"curl -s -c /tmp/cookies.txt {base_url}/accounts/login/ > /tmp/login.html")
      csrf_token = machine.succeed(
          "grep -oP 'csrfmiddlewaretoken.*?value=\"\\K[^\"]+' /tmp/login.html"
      ).strip()
      login_response = machine.succeed(f"""
          curl -s -b /tmp/cookies.txt -c /tmp/cookies.txt -w '\n%{{http_code}}'
          -H 'X-Real-IP: 127.0.0.1'
          -H 'Origin: {base_url}' -H 'Referer: {base_url}/accounts/login/'
          -d 'csrfmiddlewaretoken={csrf_token}&login=testuser&password=testpass123'
          {base_url}/accounts/login/
      """)
      # Successful login returns 302 redirect
      assert "302" in login_response, f"Login failed: {login_response[-200:]}"

      # Verify we are logged in (home page doesn't redirect to login)
      home_status = machine.succeed(f"""
          curl -s -o /dev/null -w '%{{http_code}}'
          -b /tmp/cookies.txt -c /tmp/cookies.txt {base_url}/
      """).strip()
      assert home_status == "200", f"Not logged in, got status: {home_status}"

      # Create a game entry via the manual create form
      machine.succeed(f"curl -s -b /tmp/cookies.txt -c /tmp/cookies.txt {base_url}/create > /tmp/create.html")
      csrf_token = machine.succeed(
          "grep -oP 'csrfmiddlewaretoken.*?value=\"\\K[^\"]+' /tmp/create.html | head -1"
      ).strip()
      create_response = machine.succeed(f"""
          curl -s -b /tmp/cookies.txt -c /tmp/cookies.txt -w '\n%{{http_code}}'
          -H 'Referer: {base_url}/create'
          -d 'csrfmiddlewaretoken={csrf_token}&media_type=game&title=Test+Game+Entry&status=Planning&score=&progress='
          {base_url}/create
      """)
      # Successful creation returns 302 redirect
      assert "302" in create_response, f"Create entry failed: {create_response[-500:]}"

      # Verify the game appears in the games list
      machine.succeed(f"""
          curl -s -b /tmp/cookies.txt -c /tmp/cookies.txt {base_url}/medialist/game
          | grep -q 'Test Game Entry'
      """)
    '';
  };

  yamtrack-nginx = pkgs.testers.nixosTest {
    name = "yamtrack-nginx";
    nodes.machine =
      { ... }:
      {
        imports = [ self.nixosModules.default ];
        environment.systemPackages = [ self.packages.${system}.default ];
        networking.hostName = "yamtrack";
        services.yamtrack = {
          enable = true;
          package = self.packages.${system}.default;
          configureNginx = true;
          hostName = "yamtrack";
        };
      };
    testScript = ''
      import json

      base_url = "http://yamtrack"

      machine.wait_for_unit("yamtrack.service")
      machine.wait_for_unit("yamtrack-celery-worker.service")
      machine.wait_for_unit("nginx.service")
      machine.wait_until_succeeds(f"curl -fs {base_url}/accounts/login/", timeout=120)

      # Verify static files are served directly by nginx
      machine.succeed(f"curl -fs {base_url}/static/js/serviceworker.js -o /dev/null")

      # Check health endpoint returns success with JSON details
      machine.wait_until_succeeds(f"curl -fs {base_url}/health/", timeout=120)
      health = machine.succeed(f"curl -s {base_url}/health/?format=json")
      health_data = json.loads(health)
      for check, status in health_data.items():
          assert status == "working" or status == "OK", f"Health check '{check}' failed: {status}"

      # Create a test user via the yamtrack service environment
      manage = "sudo -u yamtrack env DJANGO_SETTINGS_MODULE=config.settings PYTHONPATH=${self.packages.${system}.default}/lib/yamtrack DB_PATH=/var/lib/yamtrack/db/db.sqlite3 yamtrack-manage"
      machine.succeed(f"{manage} createsuperuser --noinput --username testuser --email test@test.com")
      machine.succeed(f"""{manage} shell -c "
          from django.contrib.auth import get_user_model;
          u = get_user_model().objects.get(username='testuser');
          u.set_password('testpass123'); u.save()
      " """)

      # Log in: get login page and extract CSRF token, then POST credentials
      machine.succeed(f"curl -s -c /tmp/cookies.txt {base_url}/accounts/login/ > /tmp/login.html")
      csrf_token = machine.succeed(
          "grep -oP 'csrfmiddlewaretoken.*?value=\"\\K[^\"]+' /tmp/login.html"
      ).strip()
      login_response = machine.succeed(f"""
          curl -s -b /tmp/cookies.txt -c /tmp/cookies.txt -w '\n%{{http_code}}'
          -d 'csrfmiddlewaretoken={csrf_token}&login=testuser&password=testpass123'
          {base_url}/accounts/login/
      """)
      # Successful login returns 302 redirect
      assert "302" in login_response, f"Login failed: {login_response[-200:]}"

      # Verify we are logged in (home page doesn't redirect to login)
      home_status = machine.succeed(f"""
          curl -s -o /dev/null -w '%{{http_code}}'
          -b /tmp/cookies.txt -c /tmp/cookies.txt {base_url}/
      """).strip()
      assert home_status == "200", f"Not logged in, got status: {home_status}"

      # Create a game entry via the manual create form
      machine.succeed(f"curl -s -b /tmp/cookies.txt -c /tmp/cookies.txt {base_url}/create > /tmp/create.html")
      csrf_token = machine.succeed(
          "grep -oP 'csrfmiddlewaretoken.*?value=\"\\K[^\"]+' /tmp/create.html | head -1"
      ).strip()
      create_response = machine.succeed(f"""
          curl -s -b /tmp/cookies.txt -c /tmp/cookies.txt -w '\n%{{http_code}}'
          -H 'Referer: {base_url}/create'
          -d 'csrfmiddlewaretoken={csrf_token}&media_type=game&title=Test+Game+Entry&status=Planning&score=&progress='
          {base_url}/create
      """)
      # Successful creation returns 302 redirect
      assert "302" in create_response, f"Create entry failed: {create_response[-500:]}"

      # Verify the game appears in the games list
      machine.succeed(f"""
          curl -s -b /tmp/cookies.txt -c /tmp/cookies.txt {base_url}/medialist/game
          | grep -q 'Test Game Entry'
      """)
    '';
  };

  yamtrack-playwright = pkgs.testers.nixosTest {
    name = "yamtrack-playwright";
    nodes.machine =
      { pkgs, ... }:
      {
        virtualisation.memorySize = 2048;
        environment.systemPackages = [ playwrightTestPython ];
        environment.variables = {
          PLAYWRIGHT_BROWSERS_PATH = "${pkgs.playwright-driver.browsers}";
        };
      };
    testScript = ''
      machine.wait_for_unit("multi-user.target")
      machine.succeed("""
        set -e
        cp -r ${yamtrack}/lib/yamtrack /tmp/yamtrack-test
        chmod -R u+w /tmp/yamtrack-test
        cd /tmp/yamtrack-test
        cp ${./conftest_playwright.py} conftest.py
        export DJANGO_SETTINGS_MODULE=config.test_settings
        export HOME=/tmp
        export PLAYWRIGHT_BROWSERS_PATH=${pkgs.playwright-driver.browsers}
        ${playwrightTestPython.interpreter} -m pytest \
          app/tests/test_integration.py \
          lists/tests/test_integration.py \
          --reruns=5 --reruns-delay=10 --timeout=120 \
          -v 2>&1
      """)
    '';
  };

  # Script to run the full test suite (including network-dependent tests)
  # outside the nix sandbox. Usage: nix run .#run-tests
  run-tests = pkgs.writeShellScriptBin "yamtrack-run-tests" ''
    set -euo pipefail
    WORKDIR=$(mktemp -d)
    trap 'rm -rf "$WORKDIR"' EXIT
    cp -r ${yamtrack}/lib/yamtrack/. "$WORKDIR/"
    chmod -R u+w "$WORKDIR"
    cd "$WORKDIR"
    export DJANGO_SETTINGS_MODULE=config.test_settings
    export HOME="''${HOME:-/tmp}"
    export PLAYWRIGHT_BROWSERS_PATH=${pkgs.playwright-driver.browsers}
    exec ${playwrightTestPython.interpreter} -m pytest \
      --reruns=5 --reruns-delay=10 --timeout=120 \
      "$@"
  '';
}
