{ pkgs ? import <nixpkgs> {} }:
pkgs.mkShell {
  packages = [
    (pkgs.python312.withPackages (ps: [
      ps.playwright
      ps.anthropic
    ]))
  ];
}
