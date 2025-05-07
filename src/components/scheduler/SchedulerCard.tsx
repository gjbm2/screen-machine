// Action buttons
const actionButtons = () => {
  // If the scheduler is running or paused (which means it's in the running_schedulers dict)
  if (destination.isRunning) {
    return (
      <div className="flex space-x-2">
        {destination.isPaused ? (
          <Button
            variant="outline" 
            size="sm"
            onClick={() => onToggle(destination, 'unpause')}
          >
            <Play className="h-4 w-4 mr-1" />
            Resume
          </Button>
        ) : (
          <Button
            variant="outline" 
            size="sm"
            onClick={() => onToggle(destination, 'pause')}
          >
            <Pause className="h-4 w-4 mr-1" />
            Pause
          </Button>
        )}
        <Button
          variant="destructive" 
          size="sm"
          onClick={() => onStop(destination)}
        >
          <StopCircle className="h-4 w-4 mr-1" />
          Stop
        </Button>
        <Button
          variant="ghost" 
          size="sm"
          onClick={() => onEdit(destination)}
        >
          <Edit className="h-4 w-4" />
        </Button>
      </div>
    );
  }
  
  // If the scheduler is not running
  return (
    <div className="flex space-x-2">
      {/* If there's a schedule available */}
      {destination.schedules && destination.schedules.length > 0 ? (
        <Button
          variant="outline" 
          size="sm"
          onClick={() => onStart(destination)}
        >
          <Play className="h-4 w-4 mr-1" />
          Start
        </Button>
      ) : (
        <Button
          variant="outline" 
          size="sm"
          onClick={() => onCreate(destination)}
        >
          <Plus className="h-4 w-4 mr-1" />
          Create
        </Button>
      )}
      <Button
        variant="ghost" 
        size="sm"
        onClick={() => onEdit(destination)}
      >
        <Edit className="h-4 w-4" />
      </Button>
    </div>
  );
}; 