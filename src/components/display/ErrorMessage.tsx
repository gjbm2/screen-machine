
import React from 'react';

export interface ErrorMessageProps {
  message: string;
  backgroundColor?: string;
}

export const ErrorMessage: React.FC<ErrorMessageProps> = ({
  message,
  backgroundColor = '000000'
}) => {
  return (
    <div
      className="flex items-center justify-center w-screen h-screen"
      style={{ backgroundColor: `#${backgroundColor}` }}
    >
      <div className="bg-red-50 border border-red-200 text-red-700 px-6 py-4 rounded-lg shadow-md max-w-lg">
        <h3 className="text-lg font-semibold mb-2">Error</h3>
        <p>{message}</p>
      </div>
    </div>
  );
};
