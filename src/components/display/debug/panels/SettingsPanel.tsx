
import React from 'react';
import { CardContent } from "@/components/ui/card";
import { SettingsTab } from '../SettingsTab';
import { ShowMode, PositionMode, TransitionType } from '../../types';

interface SettingsPanelProps {
  showMode: ShowMode;
  position: PositionMode;
  refreshInterval: number;
  backgroundColor: string;
  transition: TransitionType;
  setShowMode: (value: ShowMode) => void;
  setPosition: (value: PositionMode) => void;
  setRefreshInterval: (value: number) => void;
  setBackgroundColor: (value: string) => void;
  setTransition: (value: TransitionType) => void;
  resetSettings: () => void;
}

export const SettingsPanel: React.FC<SettingsPanelProps> = ({
  showMode,
  position,
  refreshInterval,
  backgroundColor,
  transition,
  setShowMode,
  setPosition,
  setRefreshInterval,
  setBackgroundColor,
  setTransition,
  resetSettings
}) => {
  return (
    <CardContent className="mt-0 flex-1 overflow-auto">
      <SettingsTab 
        showMode={showMode}
        position={position}
        refreshInterval={refreshInterval}
        backgroundColor={backgroundColor}
        transition={transition}
        setShowMode={setShowMode}
        setPosition={setPosition}
        setRefreshInterval={setRefreshInterval}
        setBackgroundColor={setBackgroundColor}
        setTransition={setTransition}
        resetSettings={resetSettings}
      />
    </CardContent>
  );
};
