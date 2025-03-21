
import React from 'react';
import PromptForm from './prompt-form/PromptForm';
import { PromptFormProps } from './prompt-form/types';

// Re-export the PromptForm component with the same interface
const PromptFormWrapper: React.FC<PromptFormProps> = (props) => {
  return <PromptForm {...props} />;
};

export default PromptFormWrapper;
