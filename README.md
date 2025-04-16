# Configurable AI Voice Agent

A fully configurable AI-powered voice agent built with Pipecat that offers customizable components for speech-to-text, language models, and text-to-speech. This system allows you to easily switch between different AI service providers and models, giving you complete flexibility to optimize for your specific use case.

## Features

- **Configurable Services**:
  - **Speech-to-Text**: Choose between Deepgram and Groq (Whisper)
  - **Language Models**: Choose between Google Gemini and Groq
  - **Text-to-Speech**: Choose between Cartesia and Deepgram
  
- **Model Selection**:
  - Select specific models for each service provider
  - UI automatically updates available models when you change providers
  - Server provides model information through dedicated API endpoint

- **Additional Options**:
  - **Search Integration**: Enable Google Search capabilities with Gemini
  - **Latency Optimization**: Configure all components for lower latency

## Quick Start

### First, set up your environment:

1. Clone this repository and navigate to the directory
2. Create and set up your environment file:
   ```bash
   cp .env.example .env
   ```
3. Add your API keys to the `.env` file:
   - `DAILY_API_KEY` - For WebRTC communication (required)
   - `DEEPGRAM_API_KEY` - For Deepgram STT/TTS (required if using Deepgram)
   - `GOOGLE_API_KEY` - For Gemini LLM (required if using Gemini)
   - `CARTESIA_API_KEY` - For Cartesia TTS (required if using Cartesia)
   - `GROQ_API_KEY` - For Groq LLM/STT (required if using Groq)

### Start the server:

1. Create and activate a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

2. Install requirements:
   ```bash
   pip install -r requirements.txt
   ```

3. Start the server:
   ```bash
   python server.py
   ```

### Connect using the client app:

1. Navigate to the client directory:
   ```bash
   cd client/javascript
   ```

2. Install dependencies:
   ```bash
   npm install
   ```

3. Run the client app:
   ```
   npm run dev
   ```

4. Visit http://localhost:5173 in your browser.

## Available Models

### Speech-to-Text (STT)

#### Deepgram
- **nova-2**: Best for general purpose, accurate transcriptions
- **nova-2-general**: Enhanced general purpose model
- **nova-2-telephony**: Optimized for telephony audio
- **nova-2-meeting**: Specialized for meeting transcription

#### Groq (Whisper)
- **whisper-large-v3-turbo**: Groq's fastest Whisper model
- **whisper-large-v3**: High accuracy Whisper model

### Language Models (LLM)

#### Google Gemini
- **gemini-1.5-flash-001**: Fastest Gemini model
- **gemini-1.5-pro-001**: Balanced speed and quality
- **gemini-1.5-ultra-001**: Most powerful Gemini model

#### Groq
- **llama-3.1-8b-instant**: Ultra-fast lightweight model
- **llama-3.3-70b-versatile**: Powerful balanced model
- **llama-3.2-90b-vision-preview**: Advanced large model

### Text-to-Speech (TTS)

#### Cartesia
- **71a7ad14-091c-4e8e-a314-022ece01c121**: British Reading Lady
- **b98e4dfe-a8ab-4e14-8cb5-a9a0abe1fd2b**: Default Male Voice
- **9e184750-08cd-427c-9d11-50cdf523848a**: Alternative Female Voice

#### Deepgram
- **aura-helios-en**: Helios (Male)
- **aura-stella-en**: Stella (Female)
- **aura-juno-en**: Juno (Female)

## API Endpoints

The server provides the following endpoints:

- **POST /connect**: Create a new room and start a bot process with the specified configuration
- **GET /models/{service_type}**: Get available models for a specific service type (stt, llm, or tts)

## Technical Architecture

### Server Components

- **FastAPI Backend**: Handles client connections, model queries, and bot management
- **Configurable Voice Agent**: Dynamically configures the AI pipeline based on selected services
- **Daily WebRTC**: Provides real-time audio communication

### Client Components

- **Modern JavaScript Interface**: Clean, intuitive UI for service configuration
- **Dynamic Model Selection**: Automatically updates available models based on service selection
- **WebRTC Integration**: Handles audio streaming and connection management

### Pipeline Architecture

The Pipecat pipeline adapts based on your configuration:

```
┌────────────┐    ┌───────────────┐    ┌────────────────┐    ┌────────────────┐    ┌────────────┐
│            │    │               │    │                │    │                │    │            │
│   Daily    │    │  Selected STT │    │  Selected LLM  │    │  Selected TTS  │    │   Daily    │
│  Transport ├───►│   Service     ├───►│   Service      ├───►│   Service      ├───►│  Transport │
│  (Input)   │    │               │    │                │    │                │    │  (Output)  │
│            │    │               │    │                │    │                │    │            │
└────────────┘    └───────────────┘    └────────────────┘    └────────────────┘    └────────────┘
```

## Requirements

- Python 3.10+
- Node.js 16+ (for JavaScript client)
- API keys for all services you intend to use
- Modern web browser with WebRTC support

## Extending the System

To add support for additional services or models:

1. Update the model dictionaries in `server.py` and `configurable_voice_agent.py`
2. Add appropriate factory functions in `configurable_voice_agent.py`
3. Import the required Pipecat service implementations