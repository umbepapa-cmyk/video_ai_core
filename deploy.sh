#!/bin/bash
# ============================================================================
# WEEK 4 - DAY 24: Production Deployment Script
# ============================================================================
# Automated deployment to cloud platforms (Render/Railway/Fly.io)

set -e  # Exit on error

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║           AppVideoAI - Production Deployment                   ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
PLATFORM="${1:-render}"  # Default to Render
ENVIRONMENT="${2:-production}"

echo -e "${BLUE}Platform:${NC} $PLATFORM"
echo -e "${BLUE}Environment:${NC} $ENVIRONMENT"
echo ""

# Validate environment variables
echo -e "${YELLOW}→${NC} Validating environment variables..."

REQUIRED_VARS=(
    "SUPABASE_URL"
    "SUPABASE_SERVICE_ROLE_KEY"
    "FAL_KEY"
)

MISSING_VARS=()
for var in "${REQUIRED_VARS[@]}"; do
    if [ -z "${!var}" ]; then
        MISSING_VARS+=("$var")
    fi
done

if [ ${#MISSING_VARS[@]} -gt 0 ]; then
    echo -e "${RED}✗${NC} Missing environment variables:"
    for var in "${MISSING_VARS[@]}"; do
        echo -e "  - ${RED}$var${NC}"
    done
    echo ""
    echo "Set them in your .env file or export them:"
    echo "  export SUPABASE_URL=your_url"
    echo "  export SUPABASE_SERVICE_ROLE_KEY=your_key"
    exit 1
fi

echo -e "${GREEN}✓${NC} All required environment variables are set"
echo ""

# Build Docker image
echo -e "${YELLOW}→${NC} Building Docker image..."
docker build -t appvideoai:latest -t appvideoai:${ENVIRONMENT} .

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓${NC} Docker image built successfully"
else
    echo -e "${RED}✗${NC} Docker build failed"
    exit 1
fi
echo ""

# Platform-specific deployment
case $PLATFORM in
    render)
        echo -e "${YELLOW}→${NC} Deploying to Render..."
        
        if [ -z "$RENDER_SERVICE_ID" ] || [ -z "$RENDER_API_KEY" ]; then
            echo -e "${RED}✗${NC} Missing Render credentials"
            echo "Set RENDER_SERVICE_ID and RENDER_API_KEY"
            exit 1
        fi
        
        # Tag for Render registry
        RENDER_REGISTRY="registry.render.com/$RENDER_SERVICE_ID"
        docker tag appvideoai:latest $RENDER_REGISTRY:latest
        
        # Push to Render
        echo -e "${YELLOW}→${NC} Pushing image to Render registry..."
        docker push $RENDER_REGISTRY:latest
        
        # Trigger deployment
        echo -e "${YELLOW}→${NC} Triggering Render deployment..."
        curl -X POST "https://api.render.com/v1/services/$RENDER_SERVICE_ID/deploys" \
            -H "Authorization: Bearer $RENDER_API_KEY" \
            -H "Content-Type: application/json" \
            -d '{"clearCache": false}'
        
        echo -e "${GREEN}✓${NC} Render deployment initiated"
        ;;
    
    railway)
        echo -e "${YELLOW}→${NC} Deploying to Railway..."
        
        if ! command -v railway &> /dev/null; then
            echo -e "${RED}✗${NC} Railway CLI not installed"
            echo "Install: npm install -g @railway/cli"
            exit 1
        fi
        
        # Deploy with Railway CLI
        railway up
        
        echo -e "${GREEN}✓${NC} Railway deployment completed"
        ;;
    
    flyio)
        echo -e "${YELLOW}→${NC} Deploying to Fly.io..."
        
        if ! command -v flyctl &> /dev/null; then
            echo -e "${RED}✗${NC} Fly CLI not installed"
            echo "Install: curl -L https://fly.io/install.sh | sh"
            exit 1
        fi
        
        # Deploy with Fly CLI
        flyctl deploy --remote-only
        
        echo -e "${GREEN}✓${NC} Fly.io deployment completed"
        ;;
    
    docker-compose)
        echo -e "${YELLOW}→${NC} Deploying with Docker Compose (local/VPS)..."
        
        # Deploy with docker-compose
        docker-compose -f docker-compose.prod.yml up -d
        
        echo -e "${GREEN}✓${NC} Docker Compose deployment completed"
        echo ""
        echo "Services:"
        docker-compose -f docker-compose.prod.yml ps
        ;;
    
    *)
        echo -e "${RED}✗${NC} Unknown platform: $PLATFORM"
        echo "Supported platforms: render, railway, flyio, docker-compose"
        exit 1
        ;;
esac

echo ""
echo -e "${GREEN}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║               🚀 Deployment Successful! 🚀                     ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Post-deployment checks
echo -e "${YELLOW}→${NC} Running post-deployment checks..."
echo ""

# Wait for service to be ready
echo -e "${YELLOW}→${NC} Waiting for service to be healthy..."
sleep 10

# Health check (update URL based on platform)
if [ "$PLATFORM" = "docker-compose" ]; then
    HEALTH_URL="http://localhost:8000/health"
else
    echo -e "${YELLOW}⚠${NC}  Manual health check required at your deployment URL"
    echo "   Example: curl https://your-app.onrender.com/health"
fi

if [ ! -z "$HEALTH_URL" ]; then
    RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" $HEALTH_URL || echo "000")
    
    if [ "$RESPONSE" = "200" ]; then
        echo -e "${GREEN}✓${NC} Health check passed (HTTP $RESPONSE)"
    else
        echo -e "${YELLOW}⚠${NC}  Health check returned HTTP $RESPONSE"
        echo "   The service might still be starting up"
    fi
fi

echo ""
echo "Next steps:"
echo "  1. Configure your domain and SSL certificate"
echo "  2. Set up monitoring (Sentry, New Relic, etc.)"
echo "  3. Configure payment gateway webhooks"
echo "  4. Test the application thoroughly"
echo ""
