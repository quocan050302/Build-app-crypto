/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        charcoal: {
          900: '#1a1a1a',
          800: '#2d2d2d',
          700: '#3d3d3d',
        },
        aurum: {
          500: '#FFD700',
          600: '#D4AF37'
        }
      }
    },
  },
  plugins: [],
}
