{
  self,
  pkgs,
  python,
}:
let
  django-health-check = python.pkgs.buildPythonPackage {
    pname = "django-health-check";
    version = "4.2.2";
    src = pkgs.fetchPypi {
      pname = "django_health_check";
      version = "4.2.2";
      hash = "sha256-ZvmGG+HFNgf9JgN+uMna5Y24wj40rweSvG+dePKPpkw=";
    };
    pyproject = true;
    build-system = [
      python.pkgs.flit-core
      python.pkgs.flit-scm
    ];
    env.SETUPTOOLS_SCM_PRETEND_VERSION = "4.2.2";
    dependencies = [
      python.pkgs.django
      python.pkgs.dnspython
    ];
    doCheck = false;
  };

  django-select2 = python.pkgs.buildPythonPackage {
    pname = "django-select2";
    version = "8.4.8";
    src = pkgs.fetchPypi {
      pname = "django_select2";
      version = "8.4.8";
      hash = "sha256-WS5S7//ytYUMt8mLJlcVtnBPt4RpnErt3f3Yrh/6HoE=";
    };
    pyproject = true;
    build-system = [
      python.pkgs.flit-core
      python.pkgs.flit-scm
    ];
    env.SETUPTOOLS_SCM_PRETEND_VERSION = "8.4.8";
    dependencies = [
      python.pkgs.django
      python.pkgs.django-appconf
    ];
    doCheck = false;
  };

  yamtrackDeps = with python.pkgs; [
    aiohttp
    apprise
    beautifulsoup4
    celery
    croniter
    defusedxml
    django
    django-allauth
    django-celery-beat
    django-celery-results
    django-debug-toolbar
    django-health-check # custom 4.2.2
    django-model-utils
    django-redis
    django-select2 # custom 8.4.8
    django-simple-history
    django-widget-tweaks
    gunicorn
    hiredis # redis[hiredis] in requirements.txt
    icalendar
    pillow
    psycopg
    psycopg.pool
    python-decouple
    redis
    requests
    requests-ratelimiter
    unidecode
  ];
in
{
  inherit yamtrackDeps;

  yamtrack = python.pkgs.buildPythonPackage {
    pname = "yamtrack";
    version = "unstable";
    src = self;
    pyproject = false;

    postPatch = ''
      substituteInPlace src/config/settings.py \
        --replace-fail \
          'Path(BASE_DIR / "db").mkdir(parents=True, exist_ok=True)' \
          '(Path(BASE_DIR / "db").mkdir(parents=True, exist_ok=True) if not str(BASE_DIR).startswith("/nix/store") else None)'

      substituteInPlace src/config/settings.py \
        --replace-fail \
          '"NAME": BASE_DIR / "db" / "db.sqlite3",' \
          '"NAME": config("DB_PATH", default=str(BASE_DIR / "db" / "db.sqlite3")),'

      # nixpkgs fakeredis renamed FakeRedisConnection to FakeConnection
      substituteInPlace src/config/test_settings.py \
        --replace-fail 'FakeRedisConnection' 'FakeConnection'
    '';

    propagatedBuildInputs = yamtrackDeps;

    nativeBuildInputs = [ pkgs.makeWrapper ];

    buildPhase = ''
      runHook preBuild
      export DJANGO_SETTINGS_MODULE=config.settings
      export SECRET=build-secret-not-real
      cd src
      ${python.pythonOnBuildForHost.interpreter} manage.py collectstatic --noinput
      cd ..
      runHook postBuild
    '';

    installPhase = ''
      runHook preInstall
      mkdir -p $out/lib/yamtrack
      cp -r src/. $out/lib/yamtrack/
      chmod +x $out/lib/yamtrack/manage.py
      makeWrapper $out/lib/yamtrack/manage.py $out/bin/yamtrack-manage \
        --prefix PYTHONPATH : "$PYTHONPATH"
      runHook postInstall
    '';

    passthru = {
      pythonEnv = python.withPackages (_: yamtrackDeps);
    };

    doCheck = false;
  };
}
