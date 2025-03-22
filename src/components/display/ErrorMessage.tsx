
import React from 'react';

interface ErrorMessageProps {
  message: string;
  backgroundColor?: string;
}

export const ErrorMessage: React.FC<ErrorMessageProps> = ({ message, backgroundColor = '000000' }) => {
  return (
    <div style={{
      backgroundColor: `#${backgroundColor}`,
      color: '#ffffff',
      width: '100vw',
      height: '100vh',
      margin: 0,
      padding: '20px',
      overflow: 'hidden',
      display: 'flex',
      flexDirection: 'column',
      justifyContent: 'center',
      alignItems: 'center',
      fontSize: '16px',
      textAlign: 'center',
    }}>
      <h1 style={{ marginBottom: '20px' }}>{message}</h1>
      <p>Parameters:</p>
      <ul style={{ listStyleType: 'none', padding: 0 }}>
        <li><strong>output</strong>: (required) Image to display (e.g., image.jpg or full URL)</li>
        <li><strong>show</strong>: (optional) Display mode - 'fit', 'fill', 'actual', or 'stretch' (default: 'fit')</li>
        <li><strong>position</strong>: (optional) Image position - 'center', 'top-left', etc. (default: 'center')</li>
        <li><strong>refresh</strong>: (optional) Check for image updates every X seconds (default: 5)</li>
        <li><strong>background</strong>: (optional) Background color hexcode (default: 000000)</li>
        <li><strong>caption</strong>: (optional) Text to display over the image</li>
        <li><strong>caption-position</strong>: (optional) Where to display caption (default: 'bottom-center')</li>
        <li><strong>caption-size</strong>: (optional) Font size for caption (default: '16px')</li>
        <li><strong>caption-color</strong>: (optional) Text color for caption (default: 'ffffff')</li>
        <li><strong>caption-font</strong>: (optional) Font family for caption (default: 'Arial, sans-serif')</li>
        <li><strong>data</strong>: (optional) Show image metadata (empty for all metadata or specific tag name)</li>
        <li><strong>debug</strong>: (optional) Show debug information (true or false, default: false)</li>
      </ul>
      <p style={{ marginTop: '20px' }}>
        <a href="/display?debug=true" style={{ color: '#4da6ff', textDecoration: 'underline' }}>
          Enter Debug Mode
        </a>
      </p>
    </div>
  );
};
