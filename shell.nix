let
  pkgs = import <nixpkgs> { config.allowUnfree = true; };
in
pkgs.mkShell {
  packages = with pkgs; [
    smartmontools
    (python313.withPackages (
      p: with p; [
        ipython
        uvicorn
        fastapi
        pydantic
        sqlalchemy
        jinja2
        sqlmodel
        apscheduler
        pyudev
        psutil
        pysmart
        python-multipart
      ]
    ))
  ];
}
