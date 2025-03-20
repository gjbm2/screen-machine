
# AI Image Generator App

This application uses a Python Flask backend with a React frontend.

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
The application is now launched from the Flask backend:

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

3. The React frontend will be available at `http://localhost:8080` and will communicate with the Flask backend at `http://localhost:5000/api`

## Console Logging
This application includes enhanced logging capabilities:

- Both frontend and backend logs are displayed in the console view
- Python code can log messages that will appear in the frontend console
- Logs are automatically refreshed when the console is open

## Data Files
All configuration and data files are stored in the `src/data` directory:

- `workflows.json` - Available image generation workflows
- `refiners.json` - Image refinement options
- `refiner-params.json` - Parameters for refiners
- `global-options.json` - Global application settings
- `example-prompts.json` - Example prompts for the UI
- `intro-texts.json` - Introductory text variations
- `maintenance-links.json` - Resource and documentation links
- `publish-destinations.json` - Publishing destination options

To modify any of these configurations, edit the files directly in the `src/data` directory.

## Features
- Text-to-image generation
- Image-to-image generation
- Multiple workflow options
- Image refinement options
- Batch generation
- Advanced parameter controls
