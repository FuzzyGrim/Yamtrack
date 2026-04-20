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
          ;
      };

      nixosModules.default = import ./nix/module.nix { inherit self system; };
    };
}
