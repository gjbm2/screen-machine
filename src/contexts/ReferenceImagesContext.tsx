import React, { createContext, useContext, useState } from 'react';

interface ReferenceImagesContextType {
  referenceUrls: string[];
  addReferenceUrl: (url: string, append?: boolean) => void;
  removeReferenceUrl: (url: string) => void;
  clearReferenceUrls: () => void;
}

const ReferenceImagesContext = createContext<ReferenceImagesContextType | undefined>(undefined);

export const ReferenceImagesProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [referenceUrls, setReferenceUrls] = useState<string[]>([]);

  const addReferenceUrl = (url: string, append = true) => {
    console.log('Adding reference URL:', url, 'append:', append);
    if (append) {
      setReferenceUrls(prev => [...prev, url]);
    } else {
      setReferenceUrls([url]);
    }
  };

  const removeReferenceUrl = (url: string) => {
    console.log('Removing reference URL:', url);
    setReferenceUrls(prev => prev.filter(u => u !== url));
  };

  const clearReferenceUrls = () => {
    console.log('Clearing all reference URLs');
    setReferenceUrls([]);
  };

  return (
    <ReferenceImagesContext.Provider
      value={{
        referenceUrls,
        addReferenceUrl,
        removeReferenceUrl,
        clearReferenceUrls
      }}
    >
      {children}
    </ReferenceImagesContext.Provider>
  );
};

export const useReferenceImages = () => {
  const context = useContext(ReferenceImagesContext);
  if (context === undefined) {
    throw new Error('useReferenceImages must be used within a ReferenceImagesProvider');
  }
  return context;
}; 