
import React from 'react';

interface ErrorMessageProps {
  error: string;
  backgroundColor: string;
}

export const ErrorMessage: React.FC<ErrorMessageProps> = ({ error, backgroundColor }) => {
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
      <h1 style={{ marginBottom: '20px' }}>{error}</h1>
      <p>Parameters:</p>
      <ul style={{ listStyleType: 'none', padding: 0 }}>
        <li><strong>output</strong>: (required) Image to display (e.g., image.jpg or full URL)</li>
        <li><strong>show</strong>: (optional) Display mode - 'fit', 'fill', or 'actual' (default: 'fit')</li>
        <li><strong>refresh</strong>: (optional) Check for image updates every X seconds (default: 5)</li>
        <li><strong>background</strong>: (optional) Background color hexcode (default: 000000)</li>
        <li><strong>debug</strong>: (optional) Show debug information (true or false, default: false)</li>
      </ul>
    </div>
  );
};
