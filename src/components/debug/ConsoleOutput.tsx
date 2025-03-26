import React from 'react';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { okaidia } from 'react-syntax-highlighter/dist/esm/styles/prism';

interface ConsoleOutputProps {
  logs: string[];
}

const ConsoleOutput: React.FC<ConsoleOutputProps> = ({ logs }) => {
  return (
    <div className="font-mono text-xs p-4 bg-gray-900 text-white overflow-auto whitespace-pre-wrap">
      {logs.length === 0 ? (
        <div className="text-gray-500 py-2">No logs to display</div>
      ) : (
        logs.map((log, index) => {
          // Safe check: Ensure the log is not null or undefined
          if (log === null || log === undefined) {
            return <div key={index} className="text-red-400">Invalid log entry</div>;
          }

          // Determine if the log is a JSON object that was stringified
          let isJson = false;
          let formattedLog = log;
          
          try {
            // Check if it's a stringified JSON
            if (typeof log === 'string' && (log.startsWith('{') || log.startsWith('['))) {
              const parsedLog = JSON.parse(log);
              formattedLog = JSON.stringify(parsedLog, null, 2);
              isJson = true;
            }
          } catch (e) {
            // Not a valid JSON, keep as regular string
            isJson = false;
          }
          
          // Determine if it contains an error message
          const isError = 
            log.toLowerCase().includes('error') || 
            log.toLowerCase().includes('exception') ||
            log.toLowerCase().includes('failed');

          // Determine if it's a warning
          const isWarning = 
            log.toLowerCase().includes('warning') || 
            log.toLowerCase().includes('warn');

          // Apply appropriate styling based on the log type
          const logClass = isError 
            ? "text-red-400" 
            : isWarning 
              ? "text-yellow-300" 
              : "text-green-200";

          return (
            <div key={index} className={`py-1 ${logClass}`}>
              {isJson ? (
                <SyntaxHighlighter language="json" style={okaidia} customStyle={{ background: 'transparent' }}>
                  {formattedLog}
                </SyntaxHighlighter>
              ) : (
                log
              )}
            </div>
          );
        })
      )}
    </div>
  );
};

export default ConsoleOutput;
