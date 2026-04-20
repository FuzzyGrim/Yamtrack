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
      inherit (packages) yamtrack;
    in
    {
      packages.${system}.default = yamtrack;
    };
}
