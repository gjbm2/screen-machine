
# AI Image Generator App

This application has been replatformed from a pure TypeScript/React application to a Python Flask backend with a React frontend.

## Setup Instructions

### Prerequisites
- Python 3.8 or higher
- Node.js 16 or higher
- npm or yarn

### Backend Setup
1. Create a virtual environment (recommended):
   ```
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

2. Install Python dependencies:
   ```
   pip install -r requirements.txt
   ```

3. Copy data files from src/data to the data directory:
   ```
   python utils/setup_data.py
   ```

### Frontend Setup
1. Install frontend dependencies:
   ```
   npm install
   ```

2. Build the React frontend:
   ```
   npm run build
   ```

### Running the Application
1. Start the Flask backend:
   ```
   python app.py
   ```

2. Access the application:
   Open your browser and navigate to `http://localhost:5000`

## Development Mode
If you want to run the application in development mode:

1. Start the Flask backend:
   ```
   python app.py
   ```

2. In another terminal, start the React development server:
   ```
   npm run dev
   ```

3. Update the API endpoints in the React code to point to `http://localhost:5000/api`

## Features
- Text-to-image generation
- Image-to-image generation
- Multiple workflow options
- Image refinement options
- Batch generation
- Advanced parameter controls
