{
  description = "My Computer for Nautilus, what GNOME Files should have always been";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  # This flake lives in packaging/nix/ rather than the repo root. `src = ../../.`
  # therefore points at the repository root. That resolves correctly when the
  # whole repo is the fetched tree, i.e. `github:yannmasoch/nautilus-my-computer?dir=packaging/nix`
  # or, for local testing, `nix build "path:$PWD?dir=packaging/nix"` run from the
  # repo root (NOT `nix build ./packaging/nix`, which would copy only this
  # subdirectory and cut off the parent). If parent access ever proves
  # unavailable on a given Nix version, replace the `src = ../../.` line below
  # with a pinned `src = pkgs.fetchFromGitHub { owner = "yannmasoch"; ... };`.
  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        package = pkgs.callPackage ./package.nix { src = ../../.; };
      in
      {
        packages.default = package;
        packages.nautilus-my-computer = package;
      }
    );
}
