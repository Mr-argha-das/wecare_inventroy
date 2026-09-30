/**
 * PM2 process file for WE CARE HOME HEALTHCARE billing software.
 *
 *   pm2 start ecosystem.config.js --env production
 *   pm2 logs wecare-billing
 *   pm2 restart wecare-billing
 *
 * IMPORTANT — this app stores data in Apache Feather files (not a server DB),
 * so it MUST run as a SINGLE process. Do not use exec_mode "cluster" and do not
 * raise `instances` above 1 or uvicorn --workers above 1: concurrent writers
 * would corrupt the .feather files.
 */
module.exports = {
  apps: [
    {
      name: "wecare-billing",
      // Use the virtualenv interpreter directly (no shell, no activate needed).
      script: "./.venv/bin/python",
      args: "-m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips=*",
      cwd: __dirname,
      interpreter: "none",        // script is already an executable binary
      exec_mode: "fork",          // never "cluster" — see note above
      instances: 1,               // never more than 1 — see note above
      autorestart: true,
      watch: false,
      max_restarts: 10,
      min_uptime: "20s",
      restart_delay: 3000,
      kill_timeout: 10000,        // let in-flight PDF generation finish
      max_memory_restart: "600M",
      time: true,                 // timestamp every log line
      out_file: "./logs/pm2-out.log",
      error_file: "./logs/pm2-error.log",
      merge_logs: true,
      env: {
        PYTHONUNBUFFERED: "1",
        PYTHONPATH: __dirname,
      },
      env_production: {
        PYTHONUNBUFFERED: "1",
        PYTHONPATH: __dirname,
        // Real secrets belong in the .env file, not here.
      },
    },
  ],
};
