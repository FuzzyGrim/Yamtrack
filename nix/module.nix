{
  self,
  system,
}:
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.services.yamtrack;
  pkg = cfg.package;
  pythonEnv = pkg.passthru.pythonEnv;
  stateDir = "/var/lib/yamtrack";

  env =
    {
      DJANGO_SETTINGS_MODULE = "config.settings";
      PYTHONPATH = "${pkg}/lib/yamtrack";
    }
    // lib.optionalAttrs (!cfg.database.createLocally) {
      DB_PATH = "${stateDir}/db/db.sqlite3";
    }
    // lib.optionalAttrs cfg.database.createLocally {
      DB_HOST = "/run/postgresql";
      DB_NAME = "yamtrack";
      DB_USER = "yamtrack";
      DB_PASSWORD = "";
      DB_PORT = "5432";
    }
    // lib.optionalAttrs cfg.redis.createLocally {
      REDIS_URL = "redis://localhost:6379";
    }
    // lib.optionalAttrs (cfg.secretKeyFile != null) {
      SECRET_FILE = cfg.secretKeyFile;
    }
    // lib.mapAttrs (_: toString) cfg.extraConfig;

  commonServiceConfig = {
    User = cfg.user;
    Group = cfg.group;
    WorkingDirectory = stateDir;
    StateDirectory = "yamtrack";
    Restart = "on-failure";
  };
in
{
  options.services.yamtrack = {
    enable = lib.mkEnableOption "Yamtrack media tracker";

    package = lib.mkOption {
      type = lib.types.package;
      default = self.packages.${system}.default;
      description = "The Yamtrack package to use.";
    };

    address = lib.mkOption {
      type = lib.types.str;
      default = "localhost";
      description = "Address to bind gunicorn to.";
    };

    port = lib.mkOption {
      type = lib.types.port;
      default = 8001;
      description = "Port to bind gunicorn to.";
    };

    database = {
      createLocally = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = "Create a local PostgreSQL database. When false, uses SQLite.";
      };
    };

    redis = {
      createLocally = lib.mkOption {
        type = lib.types.bool;
        default = true;
        description = "Create a local Redis instance for Yamtrack.";
      };
    };

    secretKeyFile = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = null;
      description = "Path to file containing the Django SECRET_KEY. Read via the SECRET_FILE env var.";
    };

    extraConfig = lib.mkOption {
      type = lib.types.attrs;
      default = { };
      description = "Extra environment variables for Yamtrack.";
    };

    user = lib.mkOption {
      type = lib.types.str;
      default = "yamtrack";
      description = "User account under which Yamtrack runs.";
    };

    group = lib.mkOption {
      type = lib.types.str;
      default = "yamtrack";
      description = "Group under which Yamtrack runs.";
    };
  };

  config = lib.mkIf cfg.enable {
    users.users = lib.mkIf (cfg.user == "yamtrack") {
      yamtrack = {
        inherit (cfg) group;
        isSystemUser = true;
        extraGroups = lib.optional cfg.redis.createLocally "redis-yamtrack";
      };
    };

    users.groups = lib.mkIf (cfg.group == "yamtrack") {
      yamtrack = { };
    };

    services.redis.servers.yamtrack = lib.mkIf cfg.redis.createLocally {
      enable = true;
      port = 6379;
    };

    services.postgresql = lib.mkIf cfg.database.createLocally {
      enable = true;
      ensureDatabases = [ "yamtrack" ];
      ensureUsers = [
        {
          name = "yamtrack";
          ensureDBOwnership = true;
        }
      ];
    };

    systemd.services.yamtrack = {
      description = "Yamtrack media tracker";
      wantedBy = [ "multi-user.target" ];
      requires =
        lib.optional cfg.database.createLocally "postgresql.target"
        ++ lib.optional cfg.redis.createLocally "redis-yamtrack.service";
      after =
        lib.optional cfg.database.createLocally "postgresql.target"
        ++ lib.optional cfg.redis.createLocally "redis-yamtrack.service";

      environment = env;

      preStart = ''
        ${lib.optionalString (!cfg.database.createLocally) "mkdir -p ${stateDir}/db"}
        ${pkg}/bin/yamtrack-manage migrate --noinput
      '';

      serviceConfig = commonServiceConfig // {
        ExecStart = "${pythonEnv}/bin/gunicorn config.wsgi:application --bind ${cfg.address}:${toString cfg.port} --timeout 200 --preload";
      };
    };

    systemd.services.yamtrack-celery-worker = {
      description = "Yamtrack Celery worker";
      wantedBy = [ "multi-user.target" ];
      after = [ "yamtrack.service" ];
      requires = [ "yamtrack.service" ];

      environment = env;

      serviceConfig = commonServiceConfig // {
        ExecStart = "${pythonEnv}/bin/celery --app config worker --loglevel INFO --without-mingle --without-gossip";
      };
    };

    systemd.services.yamtrack-celery-beat = {
      description = "Yamtrack Celery beat scheduler";
      wantedBy = [ "multi-user.target" ];
      after = [ "yamtrack.service" ];
      requires = [ "yamtrack.service" ];

      environment = env;

      serviceConfig = commonServiceConfig // {
        ExecStart = "${pythonEnv}/bin/celery --app config beat --loglevel INFO";
      };
    };
  };
}
