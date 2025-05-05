import { defineConfig } from 'vite';

export default defineConfig({
    server: {
        proxy: {
            // Proxy API requests to the backend server
            '/connect': {
                target: 'http://0.0.0.0:7860',
                changeOrigin: true,
            },
            '/analyze': {
                target: 'http://0.0.0.0:7860',
                changeOrigin: true,
            },
            '/login': {
                target: 'http://0.0.0.0:7860',
                changeOrigin: true,
            },
            '/register': {
                target: 'http://0.0.0.0:7860',
                changeOrigin: true,
            },
            '/join': {
                target: 'http://0.0.0.0:7860',
                changeOrigin: true,
            }
        },
    },
});