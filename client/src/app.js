import {
  LogLevel,
  RTVIClient,
  RTVIClientHelper,
  RTVIEvent,
} from '@pipecat-ai/client-js';
import { DailyTransport } from '@pipecat-ai/daily-transport';

const geminiModels = [
    { id: "gemini-2.0-flash", name: "Gemini 2.0 Flash" },
    { id: "gemini-2.0-flash-lite", name: "Gemini 2.0 Flash Lite" }
];

const groqModels = [
    { id: "llama-4-maverick-17b-128e-instruct", name: "Llama 4 Maverick" },
    { id: "llama-4-scout-17b-16e-instruct", name: "Llama 4 Scout" },
    { id: "llama-3.3-70b-versatile", name: "Llama 3.3 Versatile" }
];

class BFSIClientHelper extends RTVIClientHelper {
  constructor(contentPanel) {
    super();
    this.contentPanel = contentPanel;
  }

  handleMessage(rtviMessage) {
    console.log('BFSI Helper received message:', rtviMessage);
    
    if (rtviMessage.data) {
    }
  }

  getMessageTypes() {
    return ['bot-llm-custom-response'];
  }
}

class SalesAgentClient {
  constructor() {
    this.rtviClient = null;
    this.isSpeaking = false;
    this.authenticated = false;
    this.selectedLlmType = "gemini";
    this.selectedModel = "gemini-2.0-flash";
    
    this.setupDOMElements();
    this.setupEventListeners();
    
    this.showLoginScreen();
  }

  setupDOMElements() {
    this.authPanel = document.getElementById('auth-panel');
    this.mainApp = document.getElementById('main-app');
    this.loginFields = document.getElementById('login-fields');
    this.registerFields = document.getElementById('register-fields');
    this.showRegisterLink = document.getElementById('show-register');
    this.showLoginLink = document.getElementById('show-login');
    this.loginBtn = document.getElementById('login-btn');
    this.registerBtn = document.getElementById('register-btn');
    this.phoneInput = document.getElementById('phone');
    this.firstnameInput = document.getElementById('firstname');
    this.lastnameInput = document.getElementById('lastname');
    this.emailInput = document.getElementById('email');
    this.cityInput = document.getElementById('city');
    this.jobInput = document.getElementById('job');
    this.investorTypeSelect = document.getElementById('investor-type');
    this.authMessage = document.getElementById('auth-message');

    this.connectBtn = document.getElementById('connect-btn');
    this.disconnectBtn = document.getElementById('disconnect-btn');
    this.statusSpan = document.getElementById('connection-status');
    this.debugLog = document.getElementById('debug-log');
    this.toggleDebugBtn = document.getElementById('toggle-debug');
    this.transcriptContainer = document.getElementById('transcript-container');
    
    this.createModelSelectors();
    
    this.botAudio = document.getElementById('bot-audio');
    
    this.debugLog.parentElement.classList.add('collapsed');
    
    this.latestTranscriptItem = null;
  }

  createModelSelectors() {
    const modelSelectionDiv = document.createElement('div');
    modelSelectionDiv.className = 'model-selection';
    modelSelectionDiv.innerHTML = `
      <h3>LLM Model Selection</h3>
      <div class="form-group">
        <label for="llm-type">LLM Provider:</label>
        <select id="llm-type">
          <option value="gemini">Google Gemini</option>
          <option value="groq">Groq</option>
        </select>
      </div>
      <div class="form-group">
        <label for="model-name">Model:</label>
        <select id="model-name"></select>
      </div>
    `;
    
    const mainContent = document.querySelector('.main-content');
    const botContainer = mainContent.querySelector('.bot-container'); // Get the bot container
    
    if (mainContent && botContainer) {
        mainContent.insertBefore(modelSelectionDiv, botContainer);
    } else {
        console.error("Could not find main-content or bot-container to insert model selectors.");
        if (mainContent) {
            mainContent.appendChild(modelSelectionDiv);
        }
    }
    
    this.llmTypeSelect = document.getElementById('llm-type');
    this.modelNameSelect = document.getElementById('model-name');
    
    this.updateModelOptions("gemini");
  }

  updateModelOptions(llmType) {
    this.modelNameSelect.innerHTML = '';
    const models = llmType === "gemini" ? geminiModels : groqModels;
    
    models.forEach(model => {
      const option = document.createElement('option');
      option.value = model.id;
      option.textContent = model.name;
      this.modelNameSelect.appendChild(option);
    });
    
    this.selectedModel = models[0].id;
  }

  setupEventListeners() {
    this.showRegisterLink.addEventListener('click', (e) => {
      console.log('Show register link clicked');
      e.preventDefault();
      this.loginFields.classList.add('hidden');
      this.registerFields.classList.remove('hidden');
      console.log('Toggled visibility for register form');
    });

    this.showLoginLink.addEventListener('click', (e) => {
      console.log('Show login link clicked');
      e.preventDefault();
      this.registerFields.classList.add('hidden');
      this.loginFields.classList.remove('hidden');
      console.log('Toggled visibility for login form');
    });

    this.loginBtn.addEventListener('click', () => {
      console.log('Login button clicked');
      this.login();
    });
    this.registerBtn.addEventListener('click', () => {
      console.log('Register button clicked');
      this.register();
    });

    this.connectBtn.addEventListener('click', () => this.connect());
    this.disconnectBtn.addEventListener('click', () => this.disconnect());
    this.toggleDebugBtn.addEventListener('click', () => {
      this.debugLog.parentElement.classList.toggle('collapsed');
    });
    
    this.llmTypeSelect.addEventListener('change', (e) => {
      this.selectedLlmType = e.target.value;
      this.updateModelOptions(this.selectedLlmType);
    });
    
    this.modelNameSelect.addEventListener('change', (e) => {
      this.selectedModel = e.target.value;
    });
    
    // Bot audio state events
    this.botAudio.addEventListener('play', () => {
      this.isSpeaking = true;
    });
    
    this.botAudio.addEventListener('pause', () => {
      this.isSpeaking = false;
    });
    
    this.botAudio.addEventListener('ended', () => {
      this.isSpeaking = false;
    });

    // Add window beforeunload event to clean up any active calls
    window.addEventListener('beforeunload', () => {
      // Notify server that call is ending
      try {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/analyze', false);
        xhr.setRequestHeader('Content-Type', 'application/json');
        xhr.send(JSON.stringify({}));
      } catch (e) {
        console.error('Error in beforeunload:', e);
      }
    });
  }

  /**
   * Show login screen (called on page load/refresh)
   */
  showLoginScreen() {
    this.authPanel.classList.remove('hidden');
    this.mainApp.classList.add('hidden');
    this.authenticated = false;
  }

  /**
   * Handle user login
   */
  async login() {
    this.hideAuthMessage();
    const phone = this.phoneInput.value.trim();
    
    if (!phone) {
      this.showAuthMessage('Please enter your mobile number', 'error');
      return;
    }
    
    console.log('Attempting login with phone number:', phone);
    
    try {
      const response = await fetch('/login', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          phoneNumber: phone
        }),
      });
      
      console.log('Login response status:', response.status);
      
      if (response.ok) {
        this.authenticated = true;
        this.showMainApp();
        this.log('Login successful');
      } else {
        // Try to parse the error message if available
        try {
          const data = await response.json();
          console.error('Login error response:', data);
          this.showAuthMessage(data.message || 'Login failed. User not found.', 'error');
        } catch (jsonError) {
          console.error('Failed to parse login error:', jsonError);
          this.showAuthMessage('Login failed. User not found.', 'error');
        }
      }
    } catch (error) {
      console.error('Login fetch error:', error);
      this.showAuthMessage('Error connecting to server. Please try again.', 'error');
    }
  }
  
  /**
   * Handle user registration
   */
  async register() {
    this.hideAuthMessage();
    const phone = this.phoneInput.value.trim();
    const firstName = this.firstnameInput.value.trim();
    const lastName = this.lastnameInput.value.trim();
    const email = this.emailInput.value.trim();
    const city = this.cityInput.value.trim();
    const job = this.jobInput.value.trim();
    const investorType = this.investorTypeSelect ? this.investorTypeSelect.value : "individual";
    
    // Check all required fields
    const missingFields = [];
    if (!phone) missingFields.push("Phone Number");
    if (!firstName) missingFields.push("First Name");
    if (!lastName) missingFields.push("Last Name");
    if (!email) missingFields.push("Email");
    if (!city) missingFields.push("City");
    if (!job) missingFields.push("Job/Business");
    
    if (missingFields.length > 0) {
      this.showAuthMessage(`Please fill all required fields: ${missingFields.join(", ")}`, 'error');
      return;
    }
    
    console.log('Attempting registration with data:', { phone, firstName, lastName, email, city, job, investorType });
    
    try {
      const response = await fetch('/register', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          phoneNumber: phone,
          firstName: firstName,
          lastName: lastName,
          email: email,
          city: city,
          jobBusiness: job,
          investorType: investorType
        }),
      });
      
      console.log('Registration response status:', response.status);
      
      if (response.ok) {
        this.authenticated = true;
        this.showMainApp();
        this.log('Registration successful');
      } else {
        // Try to parse the error message if available
        try {
          const data = await response.json();
          console.error('Registration error response:', data);
          this.showAuthMessage(data.message || 'Registration failed', 'error');
        } catch (jsonError) {
          console.error('Failed to parse registration error:', jsonError);
          this.showAuthMessage('Registration failed', 'error');
        }
      }
    } catch (error) {
      console.error('Registration fetch error:', error);
      this.showAuthMessage('Error connecting to server. Please try again.', 'error');
    }
  }
  
  /**
   * Show auth message
   */
  showAuthMessage(message, type) {
    this.authMessage.textContent = message;
    this.authMessage.className = `message ${type}`;
    this.authMessage.classList.remove('hidden');
  }
  
  /**
   * Hide auth message
   */
  hideAuthMessage() {
    this.authMessage.classList.add('hidden');
  }
  
  /**
   * Show main app after successful authentication
   */
  showMainApp() {
    this.authPanel.classList.add('hidden');
    this.mainApp.classList.remove('hidden');
    
    // Clear transcript container when showing main app
    this.transcriptContainer.innerHTML = '';
    this.latestTranscriptItem = null;
    this.debugLog.innerHTML = '';
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
      roleElem.textContent = role === 'user' ? 'You' : 'Neha';
      
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
      if (!this.authenticated) {
        this.log('Error: Not authenticated');
        return;
      }
      
      // Remove the explicit GET /join call - RTVIClient will handle connection via POST /connect
      this.log('Initializing RTVI client...');
      
      // Initialize the RTVI client with a Daily WebRTC transport
      this.rtviClient = new RTVIClient({
        transport: new DailyTransport(),
        params: {
          // Pass LLM params to the /connect endpoint RTVIClient will call
          baseUrl: window.location.origin, 
          endpoints: {
            connect: `/connect?llm_type=${this.selectedLlmType}&model_name=${encodeURIComponent(this.selectedModel)}`,
          }
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
            // Optional: Automatically trigger analysis on disconnect?
            // this.analyzeCall(); 
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

      // Connect to the bot - This will now trigger the POST /connect call
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

        // Analyze call data
        await this.analyzeCall();
      } catch (error) {
        this.log(`Error disconnecting: ${error.message}`);
      }
    }
  }
  
  /**
   * Analyze call data
   */
  async analyzeCall() {
    try {
      this.log('Requesting call analysis...');
      const response = await fetch('/analyze', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({}) // Empty object since server tracks everything internally
      });
      
      if (response.ok) {
        this.log('Call analysis completed');
      } else {
        this.log(`Error analyzing call: ${response.statusText}`);
      }
    } catch (error) {
      this.log(`Error analyzing call: ${error.message}`);
    }
  }
}

// Initialize the client when the page loads
window.addEventListener('DOMContentLoaded', () => {
  new SalesAgentClient();
});