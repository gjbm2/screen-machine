
import { useState, useEffect } from 'react';
import introTexts from '@/data/intro-texts.json';

export const useIntroText = () => {
  const [randomIntroText, setRandomIntroText] = useState('');
  
  useEffect(() => {
    const introsList = introTexts.intros || [];
    const randomIndex = Math.floor(Math.random() * introsList.length);
    setRandomIntroText(introsList[randomIndex]);
  }, []);

  return { randomIntroText };
};

export default useIntroText;
