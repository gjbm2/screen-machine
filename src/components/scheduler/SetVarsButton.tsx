import React, { useState } from 'react';
import { Button } from '@/components/ui/button';
import { useToast } from '@/components/ui/use-toast';
import { Settings } from 'lucide-react';
import { SetVarsModal } from './SetVarsModal';
import apiService from '@/utils/api';

interface SetVarsButtonProps {
  destinationId: string;
  contextVars: Record<string, any>;
  onVarsSaved?: () => void;
}

export const SetVarsButton: React.FC<SetVarsButtonProps> = ({
  destinationId,
  contextVars,
  onVarsSaved
}) => {
  const [modalOpen, setModalOpen] = useState(false);
  const { toast } = useToast();

  const handleOpenModal = () => {
    if (!contextVars || Object.keys(contextVars).length === 0) {
      toast({
        title: "No context variables",
        description: "There are no context variables to edit.",
        variant: "destructive"
      });
      return;
    }
    setModalOpen(true);
  };

  const handleSaved = async () => {
    try {
      // Fetch the latest context data to ensure UI is updated
      const response = await apiService.getSchedulerContext(destinationId);
      
      if (onVarsSaved) {
        onVarsSaved();
      }
      
      // Force a full refresh of the page for now to show updated context
      // In a future implementation, we'd want to update the state more elegantly
      // without requiring a full page refresh
      window.location.reload();
    } catch (error) {
      console.error('Error refreshing context after changes:', error);
      toast({
        title: "Warning",
        description: "Variable was saved but UI may not reflect changes. Refresh the page to see updated context.",
        variant: "destructive"
      });
    }
  };

  return (
    <>
      <Button
        variant="outline"
        size="sm"
        onClick={handleOpenModal}
      >
        <Settings className="h-4 w-4 mr-2" />
        Set vars
      </Button>
      
      <SetVarsModal
        open={modalOpen}
        onOpenChange={setModalOpen}
        destination={destinationId}
        contextVars={contextVars}
        onSave={handleSaved}
      />
    </>
  );
};
