import React, { useState, useEffect, useCallback, useRef, useLayoutEffect } from 'react';
// @ts-ignore
import Form from '@rjsf/mui';
// @ts-ignore
import validator from '@rjsf/validator-ajv8';
import { createTheme, ThemeProvider } from '@mui/material/styles';
import { Button, Dialog as MuiDialog, DialogContent as MuiDialogContent, 
  DialogTitle as MuiDialogTitle, DialogActions, Tabs, Tab, 
  Select, MenuItem, TextField, Dialog, DialogTitle, DialogContent, List, ListItem, ListItemText } from '@mui/material';
import { Check, X, Save, FolderOpen } from 'lucide-react';
import apiService from '../../utils/api';
import { toast } from 'sonner';

interface SchemaEditModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  destination: string;
  schema: string;
  initialData: any;
  saveEndpoint: string;
  saveMethod: string;
  onSave: () => void;
  scriptsDirectory?: string; // Optional scripts directory path
}

// Create a basic theme
const theme = createTheme();

export const SchemaEditModal: React.FC<SchemaEditModalProps> = ({
  open,
  onOpenChange,
  destination,
  schema,
  initialData,
  saveEndpoint,
  saveMethod,
  onSave,
  scriptsDirectory
}) => {
  // Only log on initial render with useEffect instead of every render
  useEffect(() => {
    if (process.env.NODE_ENV === 'development') {
      console.log('MODAL: Schema initialized with type:', typeof schema);
      console.log('MODAL: Initial data:', initialData);
    }
  }, [schema, initialData]);
  
  const [error, setError] = useState<string | null>(null);
  const [formData, setFormData] = useState<any>(initialData || {});
  const [activeTab, setActiveTab] = useState(0);
  const [isValid, setIsValid] = useState<boolean>(true);
  const [rawText, setRawText] = useState<string>(JSON.stringify(initialData || {}, null, 2));
  const [isUpdatingFromTab, setIsUpdatingFromTab] = useState(false);
  const [isUpdatingFromEditor, setIsUpdatingFromEditor] = useState(false);
  const initializedRef = useRef(false);
  const formKey = useRef(0);
  
  // For file operations
  const [scriptFiles, setScriptFiles] = useState<string[]>([]);
  const [loadDialogOpen, setLoadDialogOpen] = useState(false);
  const [saveDialogOpen, setSaveDialogOpen] = useState(false);
  const [saveFileName, setSaveFileName] = useState("");
  
  // Use useLayoutEffect to ensure form data is set synchronously before first render
  useLayoutEffect(() => {
    if (initialData) {
      setFormData(initialData);
      setRawText(JSON.stringify(initialData, null, 2));
      // Increment form key to force re-render
      formKey.current += 1;
    }
  }, [initialData]);

  // Force sync between tabs when component mounts and whenever modal opens
  useEffect(() => {
    if (open) {
      // Ensure both form data and raw text are synchronized
      if (initialData) {
        setFormData(initialData);
        setRawText(JSON.stringify(initialData, null, 2));
        initializedRef.current = true;
        // Increment form key to force re-render
        formKey.current += 1;
      }
      
      // Start with Editor tab active
      setActiveTab(0);
    }
  }, [open, initialData]);

  // Handle tab switching
  useEffect(() => {
    // Prevent circular updates
    if (isUpdatingFromTab) {
      setIsUpdatingFromTab(false);
      return;
    }

    setIsUpdatingFromTab(true);
    if (activeTab === 0) {
      // When switching to form tab, increment form key to force re-render
      formKey.current += 1;
    } else if (activeTab === 1) {
      // When switching to raw tab, update rawText from formData
      setRawText(JSON.stringify(formData, null, 2));
    }
  }, [activeTab]);

  // This effect handles parsing rawText when editing in raw mode
  useEffect(() => {
    // Only process rawText changes when in raw tab and not during tab switch
    if (activeTab !== 1 || isUpdatingFromTab) return;

    const timeoutId = setTimeout(() => {
      try {
        const newData = JSON.parse(rawText);
        // If we're already setting form data from the editor, don't update again
        if (!isUpdatingFromEditor) {
          setFormData(newData);
          // Increment form key to force re-render when switching back
          formKey.current += 1;
        }
        setIsValid(validateJson(newData));
      } catch {
        setIsValid(false);
      }
    }, 500);

    return () => clearTimeout(timeoutId);
  }, [rawText, activeTab, isUpdatingFromTab]);

  const validateJson = useCallback((json: any) => {
    try {
      const schemaObj = JSON.parse(schema);
      const result = validator.validateFormData(json, schemaObj);
      return result.errors.length === 0;
    } catch {
      return false;
    }
  }, [schema]);

  // Load script files from directory
  const fetchScriptFiles = async () => {
    if (!scriptsDirectory) return;
    
    try {
      const files = await apiService.listFiles(scriptsDirectory);
      
      // Filter to only show .json files
      const jsonFiles = files.filter((file: string) => 
        file.endsWith('.json')
      );
      setScriptFiles(jsonFiles);
    } catch (error) {
      console.error('Error fetching script files:', error instanceof Error ? error.message : String(error));
      setError('Failed to load script files');
      toast.error('Failed to load script files');
    }
  };

  // Load a script file
  const handleLoadScript = async (filename: string) => {
    if (!scriptsDirectory) return;
    
    try {
      const filePath = `${scriptsDirectory}/${filename}`;
      const data = await apiService.readFile(filePath);
      
      if (data) {
        setFormData(data);
        setRawText(JSON.stringify(data, null, 2));
        formKey.current += 1; // Force form re-render
        setLoadDialogOpen(false);
        toast.success(`Loaded ${filename}`);
      }
    } catch (error) {
      console.error('Error loading script file:', error instanceof Error ? error.message : String(error));
      setError('Failed to load script file');
      toast.error('Failed to load script file');
    }
  };

  // Save script to file
  const handleSaveScript = async () => {
    if (!scriptsDirectory || !saveFileName) return;
    
    // Ensure filename has .json extension
    const filename = saveFileName.endsWith('.json') 
      ? saveFileName 
      : `${saveFileName}.json`;
    
    try {
      const filePath = `${scriptsDirectory}/${filename}`;
      const content = JSON.stringify(formData, null, 2);
      
      await apiService.writeFile(filePath, content);
      
      setSaveDialogOpen(false);
      setSaveFileName("");
      // Refresh the file list
      fetchScriptFiles();
      toast.success(`Saved ${filename}`);
    } catch (error) {
      console.error('Error saving script file:', error instanceof Error ? error.message : String(error));
      setError('Failed to save script file');
      toast.error('Failed to save script file');
    }
  };

  const handleFormChange = (e: any) => {
    // Make sure formData exists before using it
    if (!e.formData) return;
    
    setIsUpdatingFromEditor(true);
    setFormData(e.formData);
    
    // Only update rawText when in the raw tab
    if (activeTab === 1) {
      setRawText(JSON.stringify(e.formData, null, 2));
    }
    setIsUpdatingFromEditor(false);
  };

  const handleRawTextChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setRawText(e.target.value);
  };

  const handleSubmit = async (data: any) => {
    if (!validateJson(data)) {
      setError('Invalid form data');
      return;
    }

    try {
      // Only log in development mode
      if (process.env.NODE_ENV === 'development') {
        console.log("Submitting data:", data);
      }
      const response = await fetch(saveEndpoint, {
        method: saveMethod,
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(data),
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.error || 'Failed to save');
      }

      onSave();
      onOpenChange(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save');
    }
  };

  const handleCancel = () => {
    onOpenChange(false);
  };

  // Parse schema
  let schemaObj = {};
  try {
    schemaObj = JSON.parse(schema);
  } catch (e) {
    if (process.env.NODE_ENV === 'development') {
      console.error('Error parsing schema:', e);
    }
  }

  return (
    <ThemeProvider theme={theme}>
      <MuiDialog 
        open={open} 
        onClose={() => onOpenChange(false)}
        maxWidth="lg"
        fullWidth
        style={{ zIndex: 1300 }}
      >
        <MuiDialogTitle>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span>Edit Schedule for {destination}</span>
            {scriptsDirectory && (
              <div style={{ display: 'flex', gap: '8px' }}>
                <Button
                  variant="outlined"
                  size="small"
                  startIcon={<FolderOpen size={16} />}
                  onClick={() => {
                    fetchScriptFiles();
                    setLoadDialogOpen(true);
                  }}
                >
                  Load
                </Button>
                <Button
                  variant="outlined"
                  size="small"
                  startIcon={<Save size={16} />}
                  onClick={() => setSaveDialogOpen(true)}
                >
                  Save
                </Button>
              </div>
            )}
          </div>
        </MuiDialogTitle>
        
        <Tabs 
          value={activeTab} 
          onChange={(_, newValue) => setActiveTab(newValue)}
          style={{ borderBottom: '1px solid #e0e0e0' }}
        >
          <Tab label="Editor" />
          <Tab label={
            <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
              Raw {isValid ? <Check style={{ width: '16px', height: '16px', color: 'green' }} /> : 
              <X style={{ width: '16px', height: '16px', color: 'red' }} />}
            </div>
          } />
          <Tab label="Schema" />
        </Tabs>

        <MuiDialogContent dividers style={{ height: '70vh', padding: '16px', overflow: 'auto' }}>
          {error && (
            <div style={{ color: 'red', padding: '16px' }}>{error}</div>
          )}
          
          {activeTab === 0 ? (
            <Form
              key={`form-${formKey.current}`}
              schema={schemaObj}
              formData={formData}
              validator={validator}
              onChange={handleFormChange}
              onSubmit={({ formData }) => handleSubmit(formData)}
              liveValidate={false}
              showErrorList={false}
              noHtml5Validate={true}
            >
              {/* Remove default Submit button by using empty children */}
              <></>
            </Form>
          ) : activeTab === 1 ? (
            <div style={{ padding: '16px', height: '100%' }}>
              <textarea
                style={{ width: '100%', height: '100%', fontFamily: 'monospace', padding: '8px', border: '1px solid #ccc', borderRadius: '4px' }}
                value={rawText}
                onChange={handleRawTextChange}
              />
            </div>
          ) : (
            <div style={{ padding: '16px', height: '100%' }}>
              <pre style={{ width: '100%', height: '100%', fontFamily: 'monospace', padding: '8px', border: '1px solid #ccc', borderRadius: '4px', overflow: 'auto' }}>
                {JSON.stringify(schemaObj, null, 2)}
              </pre>
            </div>
          )}
        </MuiDialogContent>

        <DialogActions style={{ padding: '16px', borderTop: '1px solid #e0e0e0' }}>
          <Button onClick={handleCancel} variant="outlined" color="secondary">
            Cancel
          </Button>
          <Button 
            onClick={() => handleSubmit(formData)} 
            variant="contained" 
            color="primary"
            disabled={!isValid}
          >
            Save
          </Button>
        </DialogActions>
      </MuiDialog>

      {/* Load file dialog */}
      {scriptsDirectory && (
        <Dialog
          open={loadDialogOpen}
          onClose={() => setLoadDialogOpen(false)}
          maxWidth="sm"
          fullWidth
        >
          <DialogTitle>Load Script</DialogTitle>
          <DialogContent>
            {scriptFiles.length === 0 ? (
              <p>No script files found in {scriptsDirectory}</p>
            ) : (
              <List>
                {scriptFiles.map((file) => (
                  <ListItem 
                    key={file}
                    onClick={() => handleLoadScript(file)}
                  >
                    <ListItemText primary={file} />
                  </ListItem>
                ))}
              </List>
            )}
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setLoadDialogOpen(false)} color="primary">
              Cancel
            </Button>
          </DialogActions>
        </Dialog>
      )}

      {/* Save file dialog */}
      {scriptsDirectory && (
        <Dialog
          open={saveDialogOpen}
          onClose={() => setSaveDialogOpen(false)}
          maxWidth="sm"
          fullWidth
        >
          <DialogTitle>Save Script</DialogTitle>
          <DialogContent>
            <TextField
              autoFocus
              margin="dense"
              id="filename"
              label="Filename"
              type="text"
              fullWidth
              value={saveFileName}
              onChange={(e) => setSaveFileName(e.target.value)}
              helperText="The .json extension will be added automatically if omitted"
            />
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setSaveDialogOpen(false)} color="secondary">
              Cancel
            </Button>
            <Button 
              onClick={handleSaveScript} 
              color="primary"
              disabled={!saveFileName.trim()}
            >
              Save
            </Button>
          </DialogActions>
        </Dialog>
      )}
    </ThemeProvider>
  );
}; 