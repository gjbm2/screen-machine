import React from 'react';
import { Plus } from 'lucide-react';
import styles from './recent.module.css';

interface GenerateAgainCardProps {
  batchId: string;
  onGenerateAgain: (batchId: string) => void;
}

export const GenerateAgainCard: React.FC<GenerateAgainCardProps> = ({ 
  batchId, 
  onGenerateAgain 
}) => {
  const handleClick = () => {
    onGenerateAgain(batchId);
  };

  return (
    <div 
      className={styles.generateAgainCard} 
      onClick={handleClick}
    >
      <div className={styles.generateAgainInner}>
        <Plus className={styles.generateAgainIcon} />
        <span className={styles.generateAgainText}>Generate Again</span>
      </div>
    </div>
  );
};

export default GenerateAgainCard; 