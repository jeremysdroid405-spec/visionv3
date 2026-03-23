// PM2 Ecosystem Configuration for PropVision
// Usage: pm2 start ecosystem.config.js

module.exports = {
  apps: [
    {
      name: 'propvision-backend',
      cwd: '/var/www/visionv3/backend',
      script: 'server.py',
      interpreter: 'python3',  // Use system Python, not venv
      // OR if using venv, make sure it exists first:
      // interpreter: '/var/www/visionv3/backend/venv/bin/python3',
      env: {
        PORT: 8001,
        NODE_ENV: 'production'
      },
      // Use uvicorn to run FastAPI
      interpreter_args: '-m uvicorn server:app --host 0.0.0.0 --port 8001',
      script: '',  // Empty because we're using interpreter_args
      watch: false,
      max_memory_restart: '1G',
      error_file: '/var/log/propvision/backend-error.log',
      out_file: '/var/log/propvision/backend-out.log',
      log_date_format: 'YYYY-MM-DD HH:mm:ss Z'
    },
    {
      name: 'propvision-frontend',
      cwd: '/var/www/visionv3/frontend',
      script: 'npm',
      args: 'start',
      env: {
        PORT: 3000,
        NODE_ENV: 'production'
      },
      watch: false,
      error_file: '/var/log/propvision/frontend-error.log',
      out_file: '/var/log/propvision/frontend-out.log'
    }
  ]
};
