import React, { useState, useEffect, useCallback } from 'react';
import { toast } from 'sonner';
import ImageDisplay from '@/components/image-display/ImageDisplay';
import PromptForm from '@/components/prompt-form/PromptForm';
import IntroText from '@/components/IntroText';
import MainLayout from '@/components/layout/MainLayout';
import AdvancedOptionsContainer from '@/components/advanced/AdvancedOptionsContainer';
import { useImageGeneration } from '@/hooks/image-generation/use-image-generation';
import { useConsoleManagement } from '@/hooks/use-console-management';
import { useGenerationWebSocket } from '@/hooks/use-generation-websocket';
import { WebSocketMessage, AsyncGenerationUpdate } from '@/hooks/image-generation/types';
import { nanoid } from '@/lib/utils';
import typedWorkflows from '@/data/typedWorkflows';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { X } from 'lucide-react';

// Define interface for overlay messages and overlay state
interface OverlayMessage {
  html: string;
  duration: number;
  position?: string;
  clear?: boolean;
  screens?: string[];
  fadein?: number;
}

interface Overlay {
  id: string;
  html: string;
  position: string | undefined;
  visible: boolean;
  fadein?: number;
}

// Define interface for job status messages
interface JobStatusMessage {
  screens?: string | string[];
  html: string;
  duration?: number;
  position?: string;
  clear?: boolean;
  fadein?: number;
  job_id: string;
}

interface JobStatus {
  id: string;
  html: string;
  visible: boolean;
  messageHash: string; // To track if message content changed
  timeoutId?: ReturnType<typeof setTimeout>; // To store timeout ID for cleanup
}

// Utility function to generate unique IDs
function generateId(): string {
  return (typeof crypto !== "undefined" && crypto.randomUUID)
    ? crypto.randomUUID()
    : Math.random().toString(36).substring(2, 10);
}

// Utility to create hash of message content
function hashMessage(message: JobStatusMessage): string {
  return `${message.html}_${message.screens}_${message.position}`;
}

const Index = () => {
  const [advancedOptionsOpen, setAdvancedOptionsOpen] = useState(false);
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const sessionId = React.useMemo(() => nanoid(), []);
  const [overlays, setOverlays] = useState<Overlay[]>([]);
  const [jobStatuses, setJobStatuses] = useState<Record<string, JobStatus>>({});
  
  const { 
    consoleVisible, 
    consoleLogs, 
    toggleConsole, 
    clearConsole,
    addLog: addConsoleLog 
  } = useConsoleManagement();
  
  const [selectedRefiner, setSelectedRefiner] = useState('none');
  const [refinerParams, setRefinerParams] = useState<Record<string, any>>({});
  
  const [selectedPublish, setSelectedPublish] = useState('none');

  // Extract all values from useImageGeneration hook
  const {
    generatedImages,
    activeGenerations,
    imageUrl,
    currentPrompt,
    uploadedImageUrls,
    currentWorkflow,
    currentParams,
    currentGlobalParams,
    imageContainerOrder,
    expandedContainers,
    lastBatchId,
    isFirstRun,
    fullscreenRefreshTrigger,
    setCurrentPrompt,
    setUploadedImageUrls,
    setCurrentWorkflow,
    setCurrentParams,
    setCurrentGlobalParams,
    setImageContainerOrder,
    setExpandedContainers,
    handleSubmitPrompt,
    handleUseGeneratedAsInput,
    handleCreateAgain,
    handleDownloadImage,
    handleDeleteImage,
    handleReorderContainers,
    handleDeleteContainer
  } = useImageGeneration(addConsoleLog);
  
  // Handle job status message
  const handleJobStatusMessage = useCallback((message: JobStatusMessage) => {
    // Log ALL received messages for debugging
    console.log("🕵️ ALL Job Status Message Received:", message);
    
    // Check if screens property exists and extract the value for better logging
    const screens = message.screens || "";
    console.log("📋 Message screens property:", screens);
  
    // Check if this message is intended for this screen
    // Use Array.includes to check if 'index' is in the screens array
    const isForThisScreen = message.screens === 'index' || 
      message.screens === undefined || // Accept undefined screens
      (Array.isArray(message.screens) && message.screens.includes('index'));

    if (!isForThisScreen) {
      console.log("🚫 Message NOT intended for index screen. Screens:", message.screens);
      return; // Ignore messages not meant for index screen
    }

    console.log("📬 Handling Job Status Message for Index:", message);
    const messageHash = hashMessage(message);
    const jobId = message.job_id;

    // Create or update the job status
    setJobStatuses(prev => {
      // Clear any existing timeout for this job ID
      if (prev[jobId]?.timeoutId) {
        clearTimeout(prev[jobId].timeoutId);
      }
      
      // Prepare the updated job status
      const updatedStatus = {
        id: jobId,
        html: message.html,
        visible: true,
        messageHash,
        timeoutId: undefined as ReturnType<typeof setTimeout> | undefined
      };
      
      // If duration is specified, set a new timeout
      if (message.duration) {
        updatedStatus.timeoutId = setTimeout(() => {
          setJobStatuses(currentState => {
            if (!currentState[jobId]) return currentState;
            
            return {
              ...currentState,
              [jobId]: {
                ...currentState[jobId],
                visible: false,
                timeoutId: undefined
              }
            };
          });
        }, message.duration);
      }
      
      return {
        ...prev,
        [jobId]: updatedStatus
      };
    });
  }, []);

  // Handle close of job status
  const handleCloseJobStatus = useCallback((jobId: string) => {
    setJobStatuses(prev => {
      // Clear any existing timeout for this job
      if (prev[jobId]?.timeoutId) {
        clearTimeout(prev[jobId].timeoutId);
      }
      
      if (!prev[jobId]) return prev;
      
      return {
        ...prev,
        [jobId]: {
          ...prev[jobId],
          visible: false,
          timeoutId: undefined
        }
      };
    });
  }, []);
  
  // Custom hook for handling WebSocket updates
  const useOverlayWebSocket = useCallback((sessionId: string | undefined) => {
    useEffect(() => {
      if (!sessionId) return;
      
      const WS_HOST = import.meta.env.VITE_WS_HOST;
      
      let socket: WebSocket | null = null;
      let reconnectAttempts = 0;
      let reconnectTimeout: ReturnType<typeof setTimeout>;
      
      const connect = () => {
        console.log("🔄 Attempting to connect to WebSocket at:", WS_HOST);
        socket = new WebSocket(WS_HOST);
        
        socket.onopen = () => {
          console.log("🟢 Overlay WebSocket FULL Connection Log - Connected to", WS_HOST);
          reconnectAttempts = 0;
          
          // Identify this client
          const identifyMsg = JSON.stringify({ 
            type: 'identify', 
            session_id: sessionId 
          });
          console.log("📤 Sending identify message:", identifyMsg);
          socket.send(identifyMsg);
        };
        
        socket.onclose = (event) => {
          console.warn("🔌 WebSocket closed with code:", event.code, "reason:", event.reason || "No reason provided");
          reconnectAttempts++;
          const delay = Math.min(10000, 1000 * 2 ** reconnectAttempts);
          console.log(`🔄 Will attempt to reconnect in ${delay}ms (attempt #${reconnectAttempts})`);
          reconnectTimeout = setTimeout(connect, delay);
        };
        
        socket.onerror = (err) => {
          console.error("🔴 WebSocket error:", err);
          console.log("❌ Closing socket due to error");
          socket?.close();
        };
        
        socket.onmessage = (event) => {
          console.log("📬 WS raw message received:", event.data.substring(0, 200) + (event.data.length > 200 ? "..." : ""));
          try {
            const msg = JSON.parse(event.data);
            console.log("🔍 Parsed WebSocket Message type:", msg.type || "No type", "job_id:", msg.job_id || "No job_id");
            
            // Handle job status messages
            if (msg.job_id && typeof msg.html === 'string') {
              console.log("📋 Job Status Message Detected:", msg);
              handleJobStatusMessage(msg);
              return;
            }
            
            // Handle generation updates
            if (msg.type === 'generation_update') {
              console.log("🚀 Generation Update Message:", msg);
              handleGenerationUpdate(msg);
              return;
            }
            
            // Handle overlay messages
            if (msg.html) {
              console.log("🖼️ Overlay Message Detected:", msg);
              const { html, duration, position, clear } = msg;
              const id = generateId();
              const showDuration = typeof duration === "number" ? duration : 5000;
              const displayTime = Math.max(0, showDuration);
              
              setOverlays((prev) => {
                const base = clear ? [] : [...prev];
                return [...base, {
                  id,
                  html,
                  position,
                  visible: msg.fadein === 0,
                  fadein: msg.fadein
                }];
              });
              
              if (msg.fadein !== 0) {
                setTimeout(() => {
                  setOverlays((prev) =>
                    prev.map((o) => (o.id === id ? { ...o, visible: true } : o))
                  );
                }, 50);
              }
              
              setTimeout(() => {
                setOverlays((prev) =>
                  prev.map((o) => (o.id === id ? { ...o, visible: false } : o))
                );
                
                setTimeout(() => {
                  setOverlays((prev) => prev.filter((o) => o.id !== id));
                }, 5000); // OVERLAY_FADEOUT_DURATION
              }, displayTime);
            }
          } catch (err) {
            console.error("❌ WebSocket Message Parsing Error:", err);
            console.error("⚠️ Could not parse message:", event.data.substring(0, 500));
          }
        };
      };
      
      connect();
      
      return () => {
        console.log("🧹 Cleaning up WebSocket connection");
        socket?.close();
        clearTimeout(reconnectTimeout);
      };
    }, [sessionId, handleJobStatusMessage, handleGenerationUpdate]);
  }, [handleJobStatusMessage]);
  
  // Function to handle generation updates from WebSocket
  const handleGenerationUpdate = useCallback((update: AsyncGenerationUpdate) => {
    console.log("📬 Handling generation update:", update);
    
    addConsoleLog({
      type: update.status === 'error' ? 'error' : 'info',
      message: `Async generation update for batch ${update.batch_id}: ${update.status}`,
      details: update
    });
    
    if (update.status === 'progress' && update.progress !== undefined) {
      console.log(`Generation progress: ${update.progress}%`);
    } else if (update.status === 'completed') {
      if (update.images && update.images.length > 0) {
        toast.success(`Completed async generation with ${update.images.length} images`);
        console.log("Completed async images:", update.images);
      }
    } else if (update.status === 'error') {
      toast.error(`Generation failed: ${update.error || 'Unknown error'}`);
    }
  }, [addConsoleLog]);
  
  // Initialize WebSocket connection
  useOverlayWebSocket(sessionId);
  
  const handleWebSocketMessage = useCallback((message: WebSocketMessage) => {
    console.log("📬 Handling WebSocket message:", message);
    
    if (message.type === 'generation_update') {
      handleGenerationUpdate(message as AsyncGenerationUpdate);
    }
  }, [handleGenerationUpdate]);
  
  const { connected: wsConnected } = useGenerationWebSocket(handleWebSocketMessage, sessionId);
  
  useEffect(() => {
    console.log(`WebSocket connection status: ${wsConnected ? 'connected' : 'disconnected'}`);
    
    if (wsConnected) {
      addConsoleLog({
        type: 'info',
        message: 'WebSocket connected for real-time updates'
      });
    }
  }, [wsConnected, addConsoleLog]);

  const processUrlParam = (value: string): any => {
    if ((value.startsWith('"') && value.endsWith('"')) || 
        (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    
    try {
      if (value.startsWith('[') || value.startsWith('{')) {
        return JSON.parse(value);
      } else if (value === 'true') {
        return true;
      } else if (value === 'false') {
        return false;
      } else if (!isNaN(Number(value))) {
        return Number(value);
      } else {
        return value;
      }
    } catch (e) {
      console.error(`Failed to parse param value=${value}`, e);
      return value;
    }
  };

  useEffect(() => {
    const promptParam = searchParams.get('prompt');
    if (promptParam) {
      const processedPrompt = processUrlParam(promptParam);
      setCurrentPrompt(processedPrompt);
      addConsoleLog({
        type: 'info',
        message: `URL parameter: prompt=${processedPrompt}`
      });
    }
    
    const workflowParam = searchParams.get('workflow');
    if (workflowParam) {
      const workflowExists = typedWorkflows.some(w => w.id === workflowParam);
      if (workflowExists) {
        setCurrentWorkflow(workflowParam);
        addConsoleLog({
          type: 'info',
          message: `URL parameter: workflow=${workflowParam}`
        });
      } else {
        toast.error(`Workflow '${workflowParam}' not found`);
      }
    }
    
    const publishParam = searchParams.get('publish');
    if (publishParam) {
      setSelectedPublish(publishParam);
      
      addConsoleLog({
        type: 'info',
        message: `URL parameter: publish=${publishParam}`
      });
    }
    
    const refinerParam = searchParams.get('refiner');
    if (refinerParam) {
      setSelectedRefiner(refinerParam);
      addConsoleLog({
        type: 'info',
        message: `URL parameter: refiner=${refinerParam}`
      });
    }
    
    const workflowParamsObj: Record<string, any> = {};
    searchParams.forEach((value, key) => {
      if (!['prompt', 'workflow', 'refiner', 'publish', 'script', 'run'].includes(key) && !key.startsWith('refiner_')) {
        try {
          workflowParamsObj[key] = processUrlParam(value);
          
          addConsoleLog({
            type: 'info',
            message: `URL parameter: ${key}=${value} (workflow param)`
          });
        } catch (e) {
          workflowParamsObj[key] = value;
          console.error(`Failed to parse param ${key}=${value}`, e);
        }
      }
    });
    
    if (Object.keys(workflowParamsObj).length > 0) {
      setCurrentParams(prev => ({
        ...prev,
        ...workflowParamsObj
      }));
    }
    
    const refinerParamsObj: Record<string, any> = {};
    searchParams.forEach((value, key) => {
      if (key.startsWith('refiner_')) {
        const paramName = key.replace('refiner_', '');
        try {
          refinerParamsObj[paramName] = processUrlParam(value);
          
          addConsoleLog({
            type: 'info',
            message: `URL parameter: ${key}=${value} (refiner param)`
          });
        } catch (e) {
          refinerParamsObj[paramName] = value;
          console.error(`Failed to parse refiner param ${key}=${value}`, e);
        }
      }
    });
    
    if (Object.keys(refinerParamsObj).length > 0) {
      setRefinerParams(prev => ({
        ...prev,
        ...refinerParamsObj
      }));
    }
    
    const scriptParam = searchParams.get('script');
    if (scriptParam) {
      addConsoleLog({
        type: 'info',
        message: `URL parameter: script=${scriptParam} (not implemented yet)`
      });
    }
    
    const hasRunParam = searchParams.get('run');
    if (hasRunParam) {
      const timer = setTimeout(() => {
        const currentPromptValue = promptParam ? processUrlParam(promptParam) : currentPrompt;
        
        if (currentPromptValue || Object.keys(workflowParamsObj).length > 0) {
          addConsoleLog({
            type: 'info',
            message: `URL parameter: run=true (auto-generating)`
          });
          
          handleSubmitPrompt(
            currentPromptValue, 
            undefined, 
            workflowParam || currentWorkflow,
            { ...currentParams, ...workflowParamsObj },
            currentGlobalParams,
            refinerParam || selectedRefiner,
            { ...refinerParams, ...refinerParamsObj },
            publishParam || selectedPublish
          );
          
          if (searchParams.has('run')) {
            const newParams = new URLSearchParams(searchParams);
            newParams.delete('run');
            navigate(`/?${newParams.toString()}`, { replace: true });
          }
        } else {
          toast.error('Cannot auto-generate: No prompt or parameters provided');
        }
      }, 500);
      
      return () => clearTimeout(timer);
    }
  }, [
    searchParams, 
    addConsoleLog, 
    setCurrentPrompt, 
    setCurrentWorkflow, 
    setCurrentParams, 
    setSelectedRefiner, 
    setSelectedPublish, 
    handleSubmitPrompt, 
    currentPrompt, 
    currentWorkflow, 
    currentParams, 
    currentGlobalParams, 
    selectedRefiner, 
    refinerParams, 
    selectedPublish, 
    navigate
  ]);

  const handleOpenAdvancedOptions = useCallback(() => {
    console.log('Opening advanced options panel');
    setAdvancedOptionsOpen(true);
  }, []);

  const handleAdvancedOptionsOpenChange = useCallback((open: boolean) => {
    console.log('Advanced options panel open state changing to:', open);
    setAdvancedOptionsOpen(open);
  }, []);

  const handleRefinerChange = useCallback((refiner: string) => {
    console.log('Index: Refiner changed to:', refiner);
    setSelectedRefiner(refiner);
  }, []);

  const handleRefinerParamChange = useCallback((paramId: string, value: any) => {
    console.log('Index: Refiner param changed:', paramId, value);
    setRefinerParams(prev => ({
      ...prev,
      [paramId]: value
    }));
  }, []);

  const handlePublishChange = useCallback((publishId: string) => {
    console.log('Index: Publish destination changed to:', publishId);
    setSelectedPublish(publishId);
  }, []);

  const handlePromptSubmit = async (
    prompt: string,
    imageFiles?: File[] | string[],
    workflow?: string,
    params?: Record<string, any>,
    globalParams?: Record<string, any>,
    refiner?: string,
    refinerParams?: Record<string, any>,
    publish?: string
  ) => {
    try {
      console.log('Index: handlePromptSubmit called with:');
      console.log('- prompt:', prompt);
      console.log('- workflow:', workflow);
      console.log('- params:', params);
      console.log('- globalParams:', globalParams);
      console.log('- refiner:', refiner);
      console.log('- refinerParams:', refinerParams);
      console.log('- publish:', publish);

      setCurrentPrompt(prompt);

      if (workflow) {
        setCurrentWorkflow(workflow);
      }

      if (refiner) {
        setSelectedRefiner(refiner);
      }

      if (publish) {
        setSelectedPublish(publish);
      }

      let effectiveParams = params ? { ...params } : { ...currentParams };

      if (publish && publish !== 'none') {
        console.log('Adding publish destination to params:', publish);
        effectiveParams.publish_destination = publish;
      }

      setCurrentParams(effectiveParams);

      if (globalParams) {
        setCurrentGlobalParams(globalParams);
      }

      if (refinerParams) {
        setRefinerParams(refinerParams);
      }

      const allImages: (File | string)[] = [
        ...(imageFiles ?? []),
        ...uploadedImageUrls
      ];

      await handleSubmitPrompt(
        prompt,
        allImages.length > 0 ? allImages : undefined,
        workflow,
        effectiveParams,
        globalParams,
        refiner,
        refinerParams
      );
    } catch (error) {
      console.error('Error submitting prompt:', error);
      toast.error('Error generating image');
    }
  };

  // For testing: Simulate job status messages for async workflows
  useEffect(() => {
    // Check if we're in the Lovable environment
    const isLovableEnvironment = 
      typeof window !== 'undefined' && 
      (window.location.hostname === 'localhost' || 
       window.location.hostname.includes('lovable'));
    
    if (!isLovableEnvironment) {
      console.log("Not in Lovable environment, skipping test message simulation");
      return;
    }
    
    console.log("In Lovable environment, will simulate test messages for async workflows");
    
    const originalHandleSubmitPrompt = handleSubmitPrompt;
    
    const wrappedHandleSubmitPrompt = async (...args: Parameters<typeof handleSubmitPrompt>) => {
      // Check if the selected workflow is async
      const workflowConfig = typedWorkflows.find(w => w.id === (args[2] || currentWorkflow));
      if (workflowConfig?.async) {
        // Simulate a job status message for testing BEFORE calling the API
        const mockJobId = nanoid();
        const mockMessage: JobStatusMessage = {
          screens: 'index',
          html: `Generating with ${workflowConfig.name}...`,
          duration: 30000,
          job_id: mockJobId
        };
        
        console.log("Simulating job status message for async workflow:", mockMessage);
        handleJobStatusMessage(mockMessage);
        
        // Simulate an update after 3 seconds
        setTimeout(() => {
          const updatedMessage: JobStatusMessage = {
            screens: 'index',
            html: `Processing ${workflowConfig.name} results (30%)...`,
            duration: 30000,
            job_id: mockJobId // Same job ID as before
          };
          console.log("Simulating job status update:", updatedMessage);
          handleJobStatusMessage(updatedMessage);
        }, 3000);
      }
      
      // Call the original function to actually generate the image
      return await originalHandleSubmitPrompt(...args);
    };
    
    // Override the handleSubmitPrompt function temporarily
    // This is a bit of a hack but useful for testing
    (window as any).originalHandleSubmitPrompt = handleSubmitPrompt;
    (window as any).handleSubmitPrompt = wrappedHandleSubmitPrompt;
    
    return () => {
      // Restore the original function
      if ((window as any).originalHandleSubmitPrompt) {
        (window as any).handleSubmitPrompt = (window as any).originalHandleSubmitPrompt;
        delete (window as any).originalHandleSubmitPrompt;
      }
    };
  }, [handleSubmitPrompt, handleJobStatusMessage, currentWorkflow]);

  // Development-only WebSocket message simulation
  const [devJobId, setDevJobId] = useState('');
  const [devHtml, setDevHtml] = useState('Rendering...');

  const simulateWebSocketMessage = () => {
    // Only simulate in Lovable or localhost environments
    const isLovableEnvironment = 
      typeof window !== 'undefined' && 
      (window.location.hostname === 'localhost' || 
       window.location.hostname.includes('lovable'));
    
    if (!isLovableEnvironment) {
      console.warn('WebSocket simulation only works in Lovable/localhost');
      return;
    }

    const mockMessage: JobStatusMessage = {
      screens: 'index',
      html: devHtml || 'Rendering...',
      duration: 30000,
      job_id: devJobId || nanoid()
    };

    console.log('Simulating WebSocket message:', mockMessage);
    handleJobStatusMessage(mockMessage);
  };

  const overlayElements = overlays.map((o) => (
    <div
      key={o.id}
      style={{
        position: "absolute",
        zIndex: 10000,
        opacity: o.visible ? 1 : 0,
        transition: o.fadein === 0 ? "none" : `opacity ${o.fadein || 2000}ms ease`,
        pointerEvents: "none",
        ...(o.position === "top-left" && { top: "20px", left: "20px" }),
        ...(o.position === "top-center" && { top: "20px", left: "50%", transform: "translateX(-50%)" }),
        ...(o.position === "top-right" && { top: "20px", right: "20px" }),
        ...(o.position === "bottom-left" && { bottom: "20px", left: "20px" }),
        ...(o.position === "bottom-center" && { bottom: "20px", left: "50%", transform: "translateX(-50%)" }),
        ...(o.position === "bottom-right" && { bottom: "20px", right: "20px" }),
        ...(!o.position && { top: 0, left: 0, width: "100vw", height: "100vh" })
      }}
      dangerouslySetInnerHTML={{ __html: o.html }}
    />
  ));

  // Make sure to clean up all timeouts when component unmounts
  useEffect(() => {
    return () => {
      // Clean up all active timeouts
      Object.values(jobStatuses).forEach(status => {
        if (status.timeoutId) {
          clearTimeout(status.timeoutId);
        }
      });
    };
  }, [jobStatuses]);

  // Render job status elements
  const jobStatusElements = Object.values(jobStatuses)
    .filter(status => status.visible)
    .map((status, index) => (
      <div
        key={status.id}
        className="fixed left-4 bg-black/75 text-white rounded-md p-2 shadow-lg z-50 max-w-md animate-in fade-in slide-in-from-bottom-4"
        style={{
          bottom: `${4 + index * 44}px`,
          position: "fixed",
          zIndex: 9999,
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          backdropFilter: "blur(4px)",
          gap: "6px",
          maxWidth: "350px",
          fontSize: "0.875rem"
        }}
      >
        <div 
          className="flex-1 pr-2 text-sm"
          dangerouslySetInnerHTML={{ __html: status.html }} 
        />
        <button
          onClick={() => handleCloseJobStatus(status.id)}
          className="text-white/75 hover:text-white transition-colors flex-shrink-0"
          aria-label="Close notification"
        >
          <X size={16} />
        </button>
      </div>
    ));

  return (
    <>
      <MainLayout
        onToggleConsole={toggleConsole}
        consoleVisible={consoleVisible}
        onOpenAdvancedOptions={handleOpenAdvancedOptions}
        consoleLogs={consoleLogs}
        onClearConsole={clearConsole}
        isFirstRun={isFirstRun}
      >
        {isFirstRun && <IntroText />}
        
        <PromptForm 
          onSubmit={handlePromptSubmit}
          isLoading={activeGenerations.length > 0}
          currentPrompt={currentPrompt}
          isFirstRun={isFirstRun}
          onOpenAdvancedOptions={handleOpenAdvancedOptions}
          selectedWorkflow={currentWorkflow}
          selectedRefiner={selectedRefiner}
          selectedPublish={selectedPublish}
          workflowParams={currentParams}
          refinerParams={refinerParams}
          globalParams={currentGlobalParams}
          onWorkflowChange={setCurrentWorkflow}
          onRefinerChange={handleRefinerChange}
          onPublishChange={handlePublishChange}
        />
        
        <ImageDisplay 
          imageUrl={imageUrl}
          prompt={currentPrompt}
          isLoading={activeGenerations.length > 0}
          uploadedImages={uploadedImageUrls}
          generatedImages={generatedImages}
          imageContainerOrder={imageContainerOrder}
          expandedContainers={expandedContainers}
          setExpandedContainers={setExpandedContainers}
          workflow={currentWorkflow}
          generationParams={currentParams}
          onUseGeneratedAsInput={handleUseGeneratedAsInput}
          onCreateAgain={handleCreateAgain}
          onReorderContainers={handleReorderContainers}
          onDeleteImage={handleDeleteImage}
          onDeleteContainer={handleDeleteContainer}
          fullscreenRefreshTrigger={fullscreenRefreshTrigger}
        />
      </MainLayout>
      
      <AdvancedOptionsContainer
        isOpen={advancedOptionsOpen}
        onOpenChange={handleAdvancedOptionsOpenChange}
        workflows={typedWorkflows}
        selectedWorkflow={currentWorkflow}
        currentParams={currentParams}
        currentGlobalParams={currentGlobalParams}
        onWorkflowChange={setCurrentWorkflow}
        onParamsChange={setCurrentParams}
        onGlobalParamChange={(paramId: string, value: any) => {
          setCurrentGlobalParams(prev => ({
            ...prev,
            [paramId]: value
          }));
        }}
        selectedRefiner={selectedRefiner}
        refinerParams={refinerParams}
        onRefinerChange={handleRefinerChange}
        onRefinerParamChange={handleRefinerParamChange}
      />
      
      {wsConnected && (
        <div className="fixed bottom-4 right-4 p-1 px-2 bg-green-100 text-green-800 text-xs rounded-full border border-green-300 opacity-60">
          <span className="inline-block w-2 h-2 rounded-full bg-green-500 mr-1"></span>
          Connected
        </div>
      )}
      
      {overlayElements}
      
      {jobStatusElements}
      
      {(window.location.hostname === 'localhost' || window.location.hostname.includes('lovable')) && (
        <div className="fixed bottom-0 left-0 right-0 bg-gray-100 p-4 border-t flex items-center space-x-2 z-50">
          <input 
            type="text"
            placeholder="Job ID (optional)"
            value={devJobId}
            onChange={(e) => setDevJobId(e.target.value)}
            className="flex-1 p-2 border rounded"
          />
          <input 
            type="text"
            placeholder="HTML Message"
            value={devHtml}
            onChange={(e) => setDevHtml(e.target.value)}
            className="flex-1 p-2 border rounded"
          />
          <button 
            onClick={simulateWebSocketMessage}
            className="bg-blue-500 text-white p-2 rounded hover:bg-blue-600"
          >
            Simulate WS Message
          </button>
        </div>
      )}
    </>
  );
};

export default Index;
