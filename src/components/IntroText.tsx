
import React, { useState, useEffect } from 'react';
import introTexts from '@/data/intro-texts.json';

interface IntroTextProps {
  className?: string;
}

const IntroText: React.FC<IntroTextProps> = ({ className = "" }) => {
  const [randomText, setRandomText] = useState<string>("");
  
  useEffect(() => {
    // Select a random intro text from the json file
    const texts = introTexts.intros;
    const randomIndex = Math.floor(Math.random() * texts.length);
    setRandomText(texts[randomIndex]);
  }, []);
  
  return (
    <h2 className={`text-lg sm:text-xl md:text-2xl font-medium text-center mb-4 ${className}`}>
      {randomText}
    </h2>
  );
};

export default IntroText;
