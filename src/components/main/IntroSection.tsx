
import React from 'react';

interface IntroSectionProps {
  introText: string;
}

const IntroSection: React.FC<IntroSectionProps> = ({ introText }) => {
  return (
    <div className="mt-8 mb-6 text-center">
      <p className="text-lg text-muted-foreground max-w-2xl mx-auto">
        {introText}
      </p>
    </div>
  );
};

export default IntroSection;
