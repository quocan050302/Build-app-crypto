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
          950: '#0c0c0e',
          900: '#121215',
          850: '#18181c',
          800: '#1e1e24',
          750: '#26262e',
          700: '#32323c',
          600: '#464654'
        },
        aurum: {
          300: '#fde047',
          400: '#facc15',
          500: '#eab308',
          600: '#ca8a04',
          700: '#a16207'
        }
      }
    },
  },
  plugins: [],
}
