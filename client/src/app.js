/**
 * BFSI Sales Agent Client Implementation
 * 
 * This client connects to a bot server using WebRTC (via Daily).
 * It handles audio streaming and manages the connection lifecycle.
 */

import {
  LogLevel,
  RTVIClient,
  RTVIClientHelper,
  RTVIEvent,
} from '@pipecat-ai/client-js';
import { DailyTransport } from '@pipecat-ai/daily-transport';

/**
 * BFSIClientHelper handles custom message types from the bot
 */
class BFSIClientHelper extends RTVIClientHelper {
  constructor(contentPanel) {
    super();
    this.contentPanel = contentPanel;
  }

  handleMessage(rtviMessage) {
    console.log('BFSI Helper received message:', rtviMessage);
    
    // Handle any custom message types here if needed
    if (rtviMessage.data) {
      // Process custom data if present
    }
  }

  getMessageTypes() {
    return ['bot-llm-custom-response'];
  }
}

/**
 * SalesAgentClient handles the connection and media management for a real-time
 * voice interaction with the sales agent bot.
 */
class SalesAgentClient {
  constructor() {
    // Initialize client state
    this.rtviClient = null;
    this.isSpeaking = false;
    this.setupDOMElements();
    this.setupEventListeners();
  }

  /**
   * Set up references to DOM elements
   */
  setupDOMElements() {
    // UI control elements
    this.connectBtn = document.getElementById('connect-btn');
    this.disconnectBtn = document.getElementById('disconnect-btn');
    this.statusSpan = document.getElementById('connection-status');
    this.debugLog = document.getElementById('debug-log');
    this.toggleDebugBtn = document.getElementById('toggle-debug');
    this.transcriptContainer = document.getElementById('transcript-container');
    this.avatarImg = document.getElementById('avatar-img');

    // Audio element for bot's voice
    this.botAudio = document.getElementById('bot-audio');
    
    // Default state for debug panel (hidden)
    this.debugLog.parentElement.classList.add('collapsed');
    
    // Create an empty transcript display
    this.latestTranscriptItem = null;
  }

  /**
   * Set up event listeners for UI controls
   */
  setupEventListeners() {
    this.connectBtn.addEventListener('click', () => this.connect());
    this.disconnectBtn.addEventListener('click', () => this.disconnect());
    
    // Toggle debug panel visibility
    this.toggleDebugBtn.addEventListener('click', () => {
      this.debugLog.parentElement.classList.toggle('collapsed');
    });
    
    // Bot audio state events to animate avatar
    this.botAudio.addEventListener('play', () => {
      this.isSpeaking = true;
      this.animateAvatar(true);
    });
    
    this.botAudio.addEventListener('pause', () => {
      this.isSpeaking = false;
      this.animateAvatar(false);
    });
    
    this.botAudio.addEventListener('ended', () => {
      this.isSpeaking = false;
      this.animateAvatar(false);
    });
  }

  /**
   * Toggle avatar animation based on speaking state
   */
  animateAvatar(isSpeaking) {
    if (isSpeaking) {
      this.avatarImg.classList.add('speaking');
    } else {
      this.avatarImg.classList.remove('speaking');
    }
  }

  /**
   * Add a timestamped message to the debug log
   */
  log(message) {
    const entry = document.createElement('div');
    entry.textContent = `${new Date().toISOString().slice(11, 19)} - ${message}`;

    // Add styling based on message type
    if (message.startsWith('User: ')) {
      entry.className = 'user-message';
    } else if (message.startsWith('Bot: ')) {
      entry.className = 'bot-message';
    }

    this.debugLog.appendChild(entry);
    this.debugLog.scrollTop = this.debugLog.scrollHeight;
    console.log(message);
  }

  /**
   * Update transcript display in the main UI
   */
  updateTranscript(role, text, isFinal = true) {
    // For non-final transcripts, update the existing item
    if (!isFinal && this.latestTranscriptItem && this.latestTranscriptItem.dataset.role === role) {
      const contentElem = this.latestTranscriptItem.querySelector('.content');
      if (contentElem) {
        contentElem.textContent = text;
        return;
      }
    }
    
    // For final transcripts, create a new item
    if (isFinal || !this.latestTranscriptItem) {
      const item = document.createElement('div');
      item.className = `transcript-item ${role}`;
      item.dataset.role = role;
      
      const roleElem = document.createElement('div');
      roleElem.className = 'role';
      roleElem.textContent = role === 'user' ? 'You' : 'Ashok';
      
      const contentElem = document.createElement('div');
      contentElem.className = 'content';
      contentElem.textContent = text;
      
      item.appendChild(roleElem);
      item.appendChild(contentElem);
      
      this.transcriptContainer.appendChild(item);
      this.latestTranscriptItem = item;
      
      // Scroll to the bottom
      this.transcriptContainer.scrollTop = this.transcriptContainer.scrollHeight;
    }
  }

  /**
   * Update the connection status display
   */
  updateStatus(status) {
    this.statusSpan.textContent = status;
    this.log(`Status: ${status}`);
  }

  /**
   * Set up available media tracks
   */
  setupMediaTracks() {
    if (!this.rtviClient) return;

    // Get current tracks from the client
    const tracks = this.rtviClient.tracks();

    // Set up any available bot tracks
    if (tracks.bot?.audio) {
      this.setupAudioTrack(tracks.bot.audio);
    }
  }

  /**
   * Set up listeners for track events (start/stop)
   */
  setupTrackListeners() {
    if (!this.rtviClient) return;

    // Listen for new tracks starting
    this.rtviClient.on(RTVIEvent.TrackStarted, (track, participant) => {
      // Only handle non-local (bot) tracks
      if (!participant?.local && track.kind === 'audio') {
        this.setupAudioTrack(track);
      }
    });

    // Listen for tracks stopping
    this.rtviClient.on(RTVIEvent.TrackStopped, (track, participant) => {
      this.log(
        `Track stopped: ${track.kind} from ${
          participant?.name || 'unknown'
        }`
      );
    });
  }

  /**
   * Set up an audio track for playback
   */
  setupAudioTrack(track) {
    this.log('Setting up audio track');
    // Check if we're already playing this track
    if (this.botAudio.srcObject) {
      const oldTrack = this.botAudio.srcObject.getAudioTracks()[0];
      if (oldTrack?.id === track.id) return;
    }
    // Create a new MediaStream with the track and set it as the audio source
    this.botAudio.srcObject = new MediaStream([track]);
  }

  /**
   * Initialize and connect to the bot
   */
  async connect() {
    try {
      // Initialize the RTVI client with a Daily WebRTC transport
      this.rtviClient = new RTVIClient({
        transport: new DailyTransport(),
        params: {
          baseUrl: 'http://localhost:7860',
          endpoints: {
            connect: '/connect',
          },
        },
        enableMic: true,
        enableCam: false,
        callbacks: {
          // Connection state changes
          onConnected: () => {
            this.updateStatus('Connected');
            this.connectBtn.disabled = true;
            this.disconnectBtn.disabled = false;
            this.log('Client connected');
          },
          onDisconnected: () => {
            this.updateStatus('Disconnected');
            this.connectBtn.disabled = false;
            this.disconnectBtn.disabled = true;
            this.log('Client disconnected');
          },
          // Transport state changes
          onTransportStateChanged: (state) => {
            this.updateStatus(`Transport: ${state}`);
            this.log(`Transport state changed: ${state}`);
            if (state === 'ready') {
              this.setupMediaTracks();
            }
          },
          // Bot connection events
          onBotConnected: (participant) => {
            this.log(`Bot connected: ${JSON.stringify(participant)}`);
          },
          onBotDisconnected: (participant) => {
            this.log(`Bot disconnected: ${JSON.stringify(participant)}`);
          },
          onBotReady: (data) => {
            this.log(`Bot ready: ${JSON.stringify(data)}`);
            this.setupMediaTracks();
          },
          // Transcript events
          onUserTranscript: (data) => {
            if (data.final) {
              this.log(`User: ${data.text}`);
              this.updateTranscript('user', data.text, true);
            } else {
              this.updateTranscript('user', data.text, false);
            }
          },
          onBotTranscript: (data) => {
            this.log(`Bot: ${data.text}`);
            this.updateTranscript('assistant', data.text, true);
          },
          // Error handling
          onMessageError: (error) => {
            console.error('Message error:', error);
          },
          onError: (error) => {
            console.error('Error:', error);
          },
        },
      });
      
      // Set logging level
      this.rtviClient.setLogLevel(LogLevel.INFO);
      
      // Register custom message handler if needed
      this.rtviClient.registerHelper(
        'custom',
        new BFSIClientHelper(this.transcriptContainer)
      );

      // Set up listeners for media track events
      this.setupTrackListeners();

      // Initialize audio devices
      this.log('Initializing devices...');
      await this.rtviClient.initDevices();

      // Connect to the bot
      this.log('Connecting to bot...');
      await this.rtviClient.connect();

      this.log('Connection complete');
    } catch (error) {
      // Handle any errors during connection
      this.log(`Error connecting: ${error.message}`);
      this.log(`Error stack: ${error.stack}`);
      this.updateStatus('Error');

      // Clean up if there's an error
      if (this.rtviClient) {
        try {
          await this.rtviClient.disconnect();
        } catch (disconnectError) {
          this.log(`Error during disconnect: ${disconnectError.message}`);
        }
      }
    }
  }

  /**
   * Disconnect from the bot and clean up resources
   */
  async disconnect() {
    if (this.rtviClient) {
      try {
        // Disconnect the RTVI client
        await this.rtviClient.disconnect();
        this.rtviClient = null;

        // Clean up audio
        if (this.botAudio.srcObject) {
          this.botAudio.srcObject.getTracks().forEach((track) => track.stop());
          this.botAudio.srcObject = null;
        }

        // Trigger analysis after disconnect
        await this.triggerAnalysis();
      } catch (error) {
        this.log(`Error disconnecting: ${error.message}`);
      }
    }
  }
  
  /**
   * Trigger transcript analysis after call ends
   */
  async triggerAnalysis() {
    try {
      const response = await fetch('http://localhost:7860/analyze', {
        method: 'POST',
      });
      
      if (response.ok) {
        this.log('Transcript analysis completed');
      } else {
        this.log(`Error triggering analysis: ${response.statusText}`);
      }
    } catch (error) {
      this.log(`Error triggering analysis: ${error.message}`);
    }
  }
}

// Initialize the client when the page loads
window.addEventListener('DOMContentLoaded', () => {
  new SalesAgentClient();
});