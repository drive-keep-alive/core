let
  pkgs = import <nixpkgs> { config.allowUnfree = true; };
in
pkgs.mkShell {
  packages = with pkgs; [
    smartmontools
    hdparm
    e2fsprogs
    nvme-cli
    (python313.withPackages (
      p: with p; [
        ipython
        python
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
        pytest
        pytest-asyncio
      ]
    ))
  ];
}
