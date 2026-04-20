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
        services.yamtrack = {
          enable = true;
          package = self.packages.${system}.default;
        };
      };
    testScript = ''
      machine.wait_for_unit("yamtrack.service")
      machine.wait_for_unit("yamtrack-celery-worker.service")
      machine.wait_until_succeeds("curl -fs http://localhost:8001/accounts/login/", timeout=60)
      machine.wait_until_succeeds("curl -fs http://localhost:8001/health/", timeout=120)
    '';
  };

  yamtrack-postgresql = pkgs.testers.nixosTest {
    name = "yamtrack-postgresql";
    nodes.machine =
      { ... }:
      {
        imports = [ self.nixosModules.default ];
        services.yamtrack = {
          enable = true;
          package = self.packages.${system}.default;
          database.createLocally = true;
        };
      };
    testScript = ''
      machine.wait_for_unit("postgresql.service")
      machine.wait_for_unit("yamtrack.service")
      machine.wait_for_unit("yamtrack-celery-worker.service")
      machine.wait_until_succeeds("curl -fs http://localhost:8001/accounts/login/", timeout=60)
      machine.wait_until_succeeds("curl -fs http://localhost:8001/health/", timeout=120)
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
    exec ${playwrightTestPython.interpreter} -m pytest "$@"
  '';
}
