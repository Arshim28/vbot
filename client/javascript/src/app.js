/**
 * Copyright (c) 2024–2025, Daily
 *
 * SPDX-License-Identifier: BSD 2-Clause License
 */

/**
 * Configurable RTVI Client Implementation
 *
 * This client connects to an RTVI-compatible bot server using WebRTC (via Daily).
 * It handles audio streaming and manages the connection lifecycle.
 * Allows configuration of AI services (STT, LLM, TTS) through UI controls.
 */

import {
  LogLevel,
  RTVIClient,
  RTVIClientHelper,
  RTVIEvent,
} from '@pipecat-ai/client-js';
import { DailyTransport } from '@pipecat-ai/daily-transport';

// Default model definitions (fallback in case API fails)
const DEFAULT_MODELS = {
  stt: {
    deepgram: {
      "nova-3": "Latest Deepgram model with best accuracy",
      "nova-2": "General purpose transcription model"
    },
    groq: {
      "whisper-large-v3": "High accuracy multilingual model",
      "whisper-large-v3-turbo": "Fast multilingual model"
    }
  },
  llm: {
    gemini: {
      "gemini-2.0-flash": "Latest Gemini model - multimodal capabilities",
      "gemini-1.5-flash": "Fastest Gemini 1.5 model"
    },
    groq: {
      "llama-3.1-8b-instant": "Ultra-fast lightweight model",
      "llama-3.3-70b-versatile": "Powerful balanced model"
    }
  },
  tts: {
    cartesia: {
      "71a7ad14-091c-4e8e-a314-022ece01c121": "British Reading Lady"
    },
    elevenlabs: {
      "11Labs-v1/Adam": "Adam - Male, versatile",
      "11Labs-v1/Rachel": "Rachel - Female, expressive American"
    }
  }
};

class SearchResponseHelper extends RTVIClientHelper {
  constructor(contentPanel) {
    super();
    this.contentPanel = contentPanel;
  }

  handleMessage(rtviMessage) {
    console.log('SearchResponseHelper, received message:', rtviMessage);
    if (rtviMessage.data) {
      // Clear existing content
      this.contentPanel.innerHTML = '';

      // Create a container for all content
      const contentContainer = document.createElement('div');
      contentContainer.className = 'content-container';

      // Add the search_result
      if (rtviMessage.data.search_result) {
        const searchResultDiv = document.createElement('div');
        searchResultDiv.className = 'search-result';
        searchResultDiv.textContent = rtviMessage.data.search_result;
        contentContainer.appendChild(searchResultDiv);
      }

      // Add the sources
      if (rtviMessage.data.origins) {
        const sourcesDiv = document.createElement('div');
        sourcesDiv.className = 'sources';

        const sourcesTitle = document.createElement('h3');
        sourcesTitle.className = 'sources-title';
        sourcesTitle.textContent = 'Sources:';
        sourcesDiv.appendChild(sourcesTitle);

        rtviMessage.data.origins.forEach((origin) => {
          const sourceLink = document.createElement('a');
          sourceLink.className = 'source-link';
          sourceLink.href = origin.site_uri;
          sourceLink.target = '_blank';
          sourceLink.textContent = origin.site_title;
          sourcesDiv.appendChild(sourceLink);
        });

        contentContainer.appendChild(sourcesDiv);
      }

      // Add the rendered_content in an iframe
      if (rtviMessage.data.rendered_content) {
        const iframe = document.createElement('iframe');
        iframe.className = 'iframe-container';
        iframe.srcdoc = rtviMessage.data.rendered_content;
        contentContainer.appendChild(iframe);
      }

      // Append the content container to the content panel
      this.contentPanel.appendChild(contentContainer);
    }
  }

  getMessageTypes() {
    return ['bot-llm-search-response'];
  }
}

/**
 * ChatbotClient handles the connection and media management for a real-time
 * voice interaction with an AI bot.
 */
class ChatbotClient {
  constructor() {
    // Initialize client state
    this.rtviClient = null;
    this.models = { ...DEFAULT_MODELS };
    this.setupDOMElements();
    this.fetchModels().then(() => {
      this.populateModelDropdowns();
      this.setupEventListeners();
    });
  }

  /**
   * Set up references to DOM elements and create necessary media elements
   */
  setupDOMElements() {
    // Get references to UI control elements
    this.connectBtn = document.getElementById('connect-btn');
    this.disconnectBtn = document.getElementById('disconnect-btn');
    this.statusSpan = document.getElementById('connection-status');
    this.debugLog = document.getElementById('debug-log');
    this.searchResultContainer = document.getElementById('search-result-container');

    // Service selection elements
    this.sttSelect = document.getElementById('stt-select');
    this.sttModelSelect = document.getElementById('stt-model-select');
    this.llmSelect = document.getElementById('llm-select');
    this.llmModelSelect = document.getElementById('llm-model-select');
    this.ttsSelect = document.getElementById('tts-select');
    this.ttsModelSelect = document.getElementById('tts-model-select');
    this.enableSearchCheckbox = document.getElementById('enable-search');
    this.optimizeLatencyCheckbox = document.getElementById('optimize-latency');

    // Create an audio element for bot's voice output
    this.botAudio = document.createElement('audio');
    this.botAudio.autoplay = true;
    this.botAudio.playsInline = true;
    document.body.appendChild(this.botAudio);
  }

  /**
   * Fetch available models from the server
   */
  async fetchModels() {
    try {
      const services = ['stt', 'llm', 'tts'];
      for (const service of services) {
        const response = await fetch(`http://localhost:7860/models/${service}`);
        if (response.ok) {
          const data = await response.json();
          this.models[service] = data.models;
          this.log(`Fetched ${service} models from server`);
        } else {
          this.log(`Error fetching ${service} models: ${response.statusText}`);
        }
      }
    } catch (error) {
      this.log(`Error fetching models: ${error.message}`);
      this.log('Using default model definitions');
    }
  }

  /**
   * Populate model dropdown based on selected service
   */
  populateModelDropdown(serviceType, serviceSelect, modelSelect) {
    const service = serviceSelect.value;
    const models = this.models[serviceType][service] || {};
    
    // Clear current options
    modelSelect.innerHTML = '';
    
    // Add options for each available model
    Object.entries(models).forEach(([modelId, description]) => {
      const option = document.createElement('option');
      option.value = modelId;
      option.textContent = `${modelId} - ${description}`;
      modelSelect.appendChild(option);
    });
  }
  
  /**
   * Populate all model dropdowns based on current service selections
   */
  populateModelDropdowns() {
    this.populateModelDropdown('stt', this.sttSelect, this.sttModelSelect);
    this.populateModelDropdown('llm', this.llmSelect, this.llmModelSelect);
    this.populateModelDropdown('tts', this.ttsSelect, this.ttsModelSelect);
  }

  /**
   * Set up event listeners for connect/disconnect buttons and service selections
   */
  setupEventListeners() {
    this.connectBtn.addEventListener('click', () => this.connect());
    this.disconnectBtn.addEventListener('click', () => this.disconnect());

    // Update model options when service provider changes
    this.sttSelect.addEventListener('change', () => {
      this.populateModelDropdown('stt', this.sttSelect, this.sttModelSelect);
    });
    
    this.llmSelect.addEventListener('change', () => {
      this.populateModelDropdown('llm', this.llmSelect, this.llmModelSelect);
      // Toggle search option visibility
      const isGemini = this.llmSelect.value === 'gemini';
      document.getElementById('search-option').style.display = isGemini ? 'block' : 'none';
      
      if (!isGemini) {
        this.enableSearchCheckbox.checked = false;
      }
    });
    
    this.ttsSelect.addEventListener('change', () => {
      this.populateModelDropdown('tts', this.ttsSelect, this.ttsModelSelect);
    });
  }

  /**
   * Add a timestamped message to the debug log
   */
  log(message) {
    const entry = document.createElement('div');
    entry.textContent = `${new Date().toISOString()} - ${message}`;

    // Add styling based on message type
    if (message.startsWith('User: ')) {
      entry.style.color = '#2196F3'; // blue for user
    } else if (message.startsWith('Bot: ')) {
      entry.style.color = '#4CAF50'; // green for bot
    }

    this.debugLog.appendChild(entry);
    this.debugLog.scrollTop = this.debugLog.scrollHeight;
    console.log(message);
  }

  /**
   * Update the connection status display
   */
  updateStatus(status) {
    this.statusSpan.textContent = status;
    this.log(`Status: ${status}`);
  }

  /**
   * Get current service configuration from UI controls
   */
  getServiceConfig() {
    return {
      stt: this.sttSelect.value,
      stt_model: this.sttModelSelect.value,
      llm: this.llmSelect.value,
      llm_model: this.llmModelSelect.value,
      tts: this.ttsSelect.value,
      tts_model: this.ttsModelSelect.value,
      enable_search: this.enableSearchCheckbox.checked,
      optimize_latency: this.optimizeLatencyCheckbox.checked
    };
  }

  /**
   * Check for available media tracks and set them up if present
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
   * This handles new tracks being added during the session
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
        `Track stopped event: ${track.kind} from ${
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
      // Disable controls during connection
      this.connectBtn.disabled = true;
      this.sttSelect.disabled = true;
      this.sttModelSelect.disabled = true;
      this.llmSelect.disabled = true;
      this.llmModelSelect.disabled = true;
      this.ttsSelect.disabled = true;
      this.ttsModelSelect.disabled = true;
      this.enableSearchCheckbox.disabled = true;
      this.optimizeLatencyCheckbox.disabled = true;
      
      this.updateStatus('Connecting...');
      
      // Get service configuration from UI
      const serviceConfig = this.getServiceConfig();
      this.log(`Using STT: ${serviceConfig.stt} (${serviceConfig.stt_model})`);
      this.log(`Using LLM: ${serviceConfig.llm} (${serviceConfig.llm_model})`);
      this.log(`Using TTS: ${serviceConfig.tts} (${serviceConfig.tts_model})`);
      this.log(`Search enabled: ${serviceConfig.enable_search}, Low latency: ${serviceConfig.optimize_latency}`);

      // Initialize the RTVI client with a Daily WebRTC transport and our configuration
      this.rtviClient = new RTVIClient({
        transport: new DailyTransport(),
        params: {
          // Include service configuration in connect request
          baseUrl: 'http://localhost:7860',
          endpoints: {
            connect: '/connect',
          },
          connectPayload: serviceConfig
        },
        enableMic: true, // Enable microphone for user input
        enableCam: false,
        callbacks: {
          // Handle connection state changes
          onConnected: () => {
            this.updateStatus('Connected');
            this.disconnectBtn.disabled = false;
            this.log('Client connected');
          },
          onDisconnected: () => {
            this.updateStatus('Disconnected');
            this.connectBtn.disabled = false;
            this.disconnectBtn.disabled = true;
            this.sttSelect.disabled = false;
            this.sttModelSelect.disabled = false;
            this.llmSelect.disabled = false;
            this.llmModelSelect.disabled = false;
            this.ttsSelect.disabled = false;
            this.ttsModelSelect.disabled = false;
            this.enableSearchCheckbox.disabled = false;
            this.optimizeLatencyCheckbox.disabled = false;
            this.log('Client disconnected');
          },
          // Handle transport state changes
          onTransportStateChanged: (state) => {
            this.updateStatus(`Transport: ${state}`);
            this.log(`Transport state changed: ${state}`);
            if (state === 'ready') {
              this.setupMediaTracks();
            }
          },
          // Handle bot connection events
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
            // Only log final transcripts
            if (data.final) {
              this.log(`User: ${data.text}`);
            }
          },
          onBotTranscript: (data) => {
            this.log(`Bot: ${data.text}`);
          },
          // Error handling
          onMessageError: (error) => {
            console.log('Message error:', error);
          },
          onError: (error) => {
            console.log('Error:', error);
          },
        },
      });
      
      // Only register the search helper if using Gemini with search
      if (serviceConfig.llm === 'gemini' && serviceConfig.enable_search) {
        this.rtviClient.registerHelper(
          'llm',
          new SearchResponseHelper(this.searchResultContainer)
        );
      }

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

      // Re-enable controls on error
      this.connectBtn.disabled = false;
      this.sttSelect.disabled = false;
      this.sttModelSelect.disabled = false;
      this.llmSelect.disabled = false;
      this.llmModelSelect.disabled = false;
      this.ttsSelect.disabled = false;
      this.ttsModelSelect.disabled = false;
      this.enableSearchCheckbox.disabled = false;
      this.optimizeLatencyCheckbox.disabled = false;

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
   * Disconnect from the bot and clean up media resources
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

        // Clean up search results
        this.searchResultContainer.innerHTML = '';
      } catch (error) {
        this.log(`Error disconnecting: ${error.message}`);
      }
    }
  }
}

// Initialize the client when the page loads
window.addEventListener('DOMContentLoaded', () => {
  new ChatbotClient();
});