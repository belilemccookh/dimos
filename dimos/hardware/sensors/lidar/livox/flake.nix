{
  description = "Livox SDK2 — driver library for Livox LiDAR sensors";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils, ... }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };

        livox-sdk2 = pkgs.stdenv.mkDerivation rec {
          pname = "livox-sdk2";
          version = "1.2.5";

          src = pkgs.fetchFromGitHub {
            owner = "Livox-SDK";
            repo = "Livox-SDK2";
            rev = "v${version}";
            hash = "sha256-NGscO/vLiQ17yQJtdPyFzhhMGE89AJ9kTL5cSun/bpU=";
          };

          nativeBuildInputs = [ pkgs.cmake ];

          cmakeFlags = [
            "-DCMAKE_INSTALL_PREFIX=${placeholder "out"}"
            "-DBUILD_SHARED_LIBS=ON"
            "-DCMAKE_POLICY_VERSION_MINIMUM=3.5"
          ];

          preConfigure = ''
            # Skip samples, just build the SDK
            substituteInPlace CMakeLists.txt \
              --replace-fail "add_subdirectory(samples)" ""

            # Fix missing <cstdint> includes for newer GCC
            sed -i '1i #include <cstdint>' sdk_core/comm/define.h
            sed -i '1i #include <cstdint>' sdk_core/logger_handler/file_manager.h
          '';
        };
      in {
        packages = {
          default = livox-sdk2;
          inherit livox-sdk2;
        };

        devShells.default = pkgs.mkShell {
          buildInputs = [ livox-sdk2 ];
        };
      });
}
