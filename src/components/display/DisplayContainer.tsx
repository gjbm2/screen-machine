
import React from 'react';
import { DisplayParams } from './types';

interface DisplayContainerProps {
  params: DisplayParams;
  children: React.ReactNode;
}

export const DisplayContainer: React.FC<DisplayContainerProps> = ({
  params,
  children
}) => {
  const containerStyle: React.CSSProperties = {
    backgroundColor: params.debugMode ? '#f3f3f3' : `#${params.backgroundColor}`,
    width: '100vw',
    height: '100vh',
    margin: 0,
    padding: 0,
    overflow: 'hidden',
    display: 'flex',
    justifyContent: 'center',
    alignItems: 'center',
    position: 'relative',
    backgroundImage: params.debugMode ? 'url("data:image/svg+xml,%3Csvg width=\'20\' height=\'20\' viewBox=\'0 0 20 20\' xmlns=\'http://www.w3.org/2000/svg\'%3E%3Cg fill=\'%239e9e9e\' fill-opacity=\'0.1\' fill-rule=\'evenodd\'%3E%3Ccircle cx=\'3\' cy=\'3\' r=\'1\'/%3E%3Ccircle cx=\'13\' cy=\'13\' r=\'1\'/%3E%3C/g%3E%3C/svg%3E")' : 'none',
  };

  return (
    <div style={containerStyle}>
      {children}
    </div>
  );
};
