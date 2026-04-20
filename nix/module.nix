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
    // lib.optionalAttrs (cfg.trustedOrigins != [ ]) {
      CSRF = lib.concatStringsSep "," cfg.trustedOrigins;
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

    hostName = lib.mkOption {
      type = lib.types.str;
      default = "";
      example = "yamtrack.example.com";
      description = "The domain serving your Yamtrack instance. Required when configuring nginx.";
    };

    trustedOrigins = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      example = [ "https://yamtrack.example.com" ];
      description = ''
        List of trusted origins for CSRF protection (Django's CSRF_TRUSTED_ORIGINS).
        When {option}`hostName` is set, an appropriate origin is added automatically:
        `http://<hostName>` when {option}`configureNginx` is enabled (nginx serves on port 80),
        or `http://<hostName>:<port>` otherwise.
      '';
    };

    configureNginx = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Configure nginx as a reverse proxy for Yamtrack.";
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
    assertions = [
      {
        assertion = cfg.configureNginx -> cfg.hostName != "";
        message = "services.yamtrack.hostName must be set when services.yamtrack.configureNginx is enabled.";
      }
    ];

    services.yamtrack.trustedOrigins = lib.mkIf (cfg.hostName != "") (
      if cfg.configureNginx then
        [ "http://${cfg.hostName}" ]
      else
        [ "http://${cfg.hostName}:${toString cfg.port}" ]
    );

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

    services.nginx = lib.mkIf cfg.configureNginx {
      enable = true;
      recommendedProxySettings = true;
      upstreams.yamtrack.servers."127.0.0.1:${toString cfg.port}" = { };
      virtualHosts."${cfg.hostName}" = {
        locations."/static/" = {
          alias = "${pkg}/lib/yamtrack/staticfiles/";
        };
        locations."/" = {
          proxyPass = "http://yamtrack";
          proxyWebsockets = true;
        };
      };
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
