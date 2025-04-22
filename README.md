# BFSI Sales Agent Client

Client implementation for the BFSI Sales Agent using the [Pipecat JavaScript SDK](https://docs.pipecat.ai/client/js/introduction).

## Setup

1. First, ensure the bot server is running. See the main README for server setup instructions.

2. Navigate to the client directory:

```bash
cd client
```

3. Install dependencies:

```bash
npm install
```

4. Run the client app:

```bash
npm run dev
```

5. Visit http://localhost:5173 in your browser to start interacting with the sales agent.

## Features

- Real-time voice conversation with an AI sales agent
- Automatic transcription of the conversation
- Visual feedback when the agent is speaking
- Debug panel to view the full conversation transcript

## Usage

1. Click "Start Call" to connect to the sales agent
2. Speak naturally with the agent about investment options
3. Click "End Call" when you're done

After ending the call, the system will automatically analyze the conversation to better serve you in future interactions.

## Technical Details

This client uses:
- WebRTC for real-time audio communication
- Pipecat JS SDK for connection management
- Daily.co transport for WebRTC handling