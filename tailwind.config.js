/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./src/templates/**/*.html",
    "./src/**/templates/**/*.html",
    "./src/**/*.py",
  ],
  theme: {
    extend: {
      colors: {
        'base': '#0D0D0F',
        'surface-50': 'rgba(255, 255, 255, 0.04)',
        'surface-70': 'rgba(255, 255, 255, 0.07)',
        'text-primary': '#E8EAED',
        'text-muted': '#9AA0A6',
        'brand-teal': '#36E0D0',
        'brand-purple': '#7B61FF',
      },
      width: {
        'poster': '320px',
      },
      fontSize: {
        '44px': ['2.75rem', '1.1'],
        '40px': ['2.5rem', '1'],
      },
      letterSpacing: {
        'tight': '0.01em',
        'tighter': '0.02em',
        'widest': '0.12em',
      },
      boxShadow: {
        'glow-sm': '0 0 0 1px rgba(54, 224, 208, 0.35) inset, 0 0 24px rgba(54, 224, 208, 0.35)',
        'elev-1': '0 4px 24px rgba(0, 0, 0, 0.35)',
      },
      borderRadius: {
        '2xl': '24px',
      },
      screens: {
        'xs': '30rem',
      },
    },
  },
  plugins: [
    require('@tailwindcss/forms'),
  ],
}
