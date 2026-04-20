# Yamtrack - a media tracker built with Django
#
# Outputs:
#   packages.x86_64-linux.default    — Yamtrack Django application
#   packages.x86_64-linux.run-tests  — Test runner with network access
#   apps.x86_64-linux.run-tests      — `nix run .#run-tests` for full test suite
#   checks.x86_64-linux.yamtrack-unit-tests   — Sandboxed unit tests (460+)
#   checks.x86_64-linux.yamtrack-sqlite       — NixOS VM test with SQLite
#   checks.x86_64-linux.yamtrack-postgresql   — NixOS VM test with PostgreSQL
#   checks.x86_64-linux.yamtrack-playwright   — Playwright integration tests in VM
#   nixosModules.default              — NixOS service module
#
# Module options (services.yamtrack):
#   enable                — Enable Yamtrack service
#   package               — The Yamtrack package to use
#   address / port        — Gunicorn bind address (default: localhost:8001)
#   database.createLocally — Use local PostgreSQL (default: false → SQLite)
#   redis.createLocally   — Create local Redis instance (default: true)
#   secretKeyFile         — Path to file with Django SECRET_KEY
#   extraConfig           — Extra environment variables
#   user / group          — Service user/group (default: yamtrack)
{
  description = "Yamtrack - a media tracker built with Django";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs =
    { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      python = pkgs.python3;

      packages = import ./nix/package.nix { inherit self pkgs python; };
      inherit (packages) yamtrack yamtrackDeps;

      tests = import ./nix/tests.nix {
        inherit
          self
          pkgs
          system
          yamtrack
          yamtrackDeps
          python
          ;
      };
    in
    {
      packages.${system} = {
        default = yamtrack;
        inherit (tests) run-tests;
      };

      apps.${system}.run-tests = {
        type = "app";
        program = "${tests.run-tests}/bin/yamtrack-run-tests";
      };

      checks.${system} = {
        inherit (tests)
          yamtrack-unit-tests
          yamtrack-sqlite
          yamtrack-postgresql
          yamtrack-playwright
          ;
      };

      nixosModules.default = import ./nix/module.nix { inherit self system; };
    };
}
