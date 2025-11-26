# Crypto App

./stop.sh && ./start.sh
./purge-tokens.sh --ids 11006
./purge-tokens.sh -live --yes
./stop.sh && ./start.sh --history-nosort

A modern cryptocurrency dashboard built with Next.js frontend and Python FastAPI backend.

## Tech Stack

### Frontend
- **Next.js 15** - React framework
- **TypeScript** - Type safety
- **Tailwind CSS** - Styling
- **shadcn/ui** - UI components
- **Lucide React** - Icons

### Backend
- **Python 3.11** - Programming language
- **FastAPI** - Web framework
- **Pydantic** - Data validation
- **Uvicorn** - ASGI server

## Project Structure

```
crypto-app/
├── src/                    # Next.js frontend
│   ├── app/               # App router pages
│   ├── components/        # React components
│   └── lib/              # Utilities
├── server/               # Python backend
│   ├── main.py          # FastAPI application
│   ├── config.py        # Configuration
│   └── requirements.txt # Python dependencies
├── docker-compose.yml   # Docker orchestration
└── package.json         # Node.js dependencies
```

## Quick Start

### Option 1: Local Development (Recommended)

1. **Install dependencies:**
   ```bash
   # Install frontend dependencies
   npm install
   
   # Install backend dependencies
   npm run install:server
   ```

2. **Start both services:**
   ```bash
   # Start both frontend and backend simultaneously
   npm run dev:all
   ```

3. **Access the application:**
   - Frontend: http://localhost:3000
   - Backend API: http://localhost:8000
   - API Documentation: http://localhost:8000/docs

### Option 2: Docker

1. **Start with Docker Compose:**
   ```bash
   docker-compose up --build
   ```

2. **Access the application:**
   - Frontend: http://localhost:3000
   - Backend API: http://localhost:8000

### Option 3: Manual Start

1. **Start backend:**
   ```bash
   cd server
   python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
   ```

2. **Start frontend (in another terminal):**
   ```bash
   npm run dev
   ```

## Available Scripts

- `npm run dev` - Start Next.js development server
- `npm run dev:server` - Start Python backend server
- `npm run dev:all` - Start both frontend and backend
- `npm run build` - Build Next.js for production
- `npm run start` - Start production Next.js server
- `npm run lint` - Run ESLint

## API Endpoints

- `GET /` - Root endpoint
- `GET /api/crypto` - Get all cryptocurrency data
- `GET /api/crypto/{id}` - Get specific cryptocurrency data
- `GET /api/health` - Health check

## Features

- 📊 Real-time cryptocurrency price display
- 🎨 Modern, responsive UI with dark/light mode support
- 🔄 Auto-refresh functionality
- 📱 Mobile-friendly design
- ⚡ Fast API responses
- 🛡️ Type-safe development

## Development

### Adding New Components

Use shadcn/ui CLI to add new components:
```bash
npx shadcn@latest add [component-name]
```

### Adding New API Endpoints

Add new routes in `server/main.py` following the existing pattern.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## License

MIT License - see LICENSE file for details