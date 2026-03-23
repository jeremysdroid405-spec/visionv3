#!/bin/bash
# Quick Fix Script - Run this on your server NOW
# This fixes the venv issue and starts the backend

cd /var/www/visionv3/backend

echo "1. Creating virtual environment..."
python3 -m venv venv

echo "2. Installing dependencies..."
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "3. Stopping old PM2 processes..."
pm2 delete Vision-Sync 2>/dev/null || true
pm2 delete all 2>/dev/null || true

echo "4. Creating start script..."
cat > /var/www/visionv3/start_backend.sh << 'EOF'
#!/bin/bash
cd /var/www/visionv3/backend
source venv/bin/activate
exec uvicorn server:app --host 0.0.0.0 --port 8001
EOF
chmod +x /var/www/visionv3/start_backend.sh

echo "5. Starting backend with PM2..."
pm2 start /var/www/visionv3/start_backend.sh --name propvision-backend

echo "6. Waiting for startup..."
sleep 5

echo "7. Testing..."
curl -s http://localhost:8001/api/v3/status

echo ""
echo "8. Triggering initial sync..."
curl -X POST http://localhost:8001/api/v3/sync

echo ""
echo "Done! Check status with: pm2 status"
echo "View logs with: pm2 logs propvision-backend"
