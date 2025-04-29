import React, { useState, useEffect, useCallback } from 'react';
// @ts-ignore
import Form from '@rjsf/mui';
// @ts-ignore
import validator from '@rjsf/validator-ajv8';
import { createTheme, ThemeProvider } from '@mui/material/styles';
import { Button, Dialog as MuiDialog, DialogContent as MuiDialogContent, 
  DialogTitle as MuiDialogTitle, DialogActions, Tabs, Tab } from '@mui/material';
import { Check, X } from 'lucide-react';

interface SchemaEditModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  destination: string;
  schema: string;
  initialData: any;
  saveEndpoint: string;
  saveMethod: string;
  onSave: () => void;
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
  onSave
}) => {
  console.log('MODAL: Schema type:', typeof schema);
  
  const [error, setError] = useState<string | null>(null);
  const [formData, setFormData] = useState<any>({});
  const [activeTab, setActiveTab] = useState(0);
  const [isValid, setIsValid] = useState<boolean>(true);
  const [rawText, setRawText] = useState<string>("");
  
  // Initialize form data and raw text from initialData
  useEffect(() => {
    setFormData(initialData || {});
    setRawText(JSON.stringify(initialData || {}, null, 2));
  }, [initialData]);

  // Sync states when switching tabs
  useEffect(() => {
    if (activeTab === 0) {
      // When switching to form, update rawText from formData
      setRawText(JSON.stringify(formData, null, 2));
    } else if (activeTab === 1) {
      // When switching to raw, try to parse and update formData
      try {
        const newData = JSON.parse(rawText);
        setFormData(newData);
      } catch {
        // Keep current formData if parsing fails
      }
    }
  }, [activeTab, formData]);

  // Update formData from rawText when rawText changes
  useEffect(() => {
    if (activeTab === 1) {
      const timeoutId = setTimeout(() => {
        try {
          const newData = JSON.parse(rawText);
          setFormData(newData);
          setIsValid(validateJson(newData));
        } catch {
          setIsValid(false);
        }
      }, 500);

      return () => clearTimeout(timeoutId);
    }
  }, [rawText, activeTab]);

  const validateJson = useCallback((json: any) => {
    try {
      const schemaObj = JSON.parse(schema);
      const result = validator.validateFormData(json, schemaObj);
      return result.errors.length === 0;
    } catch {
      return false;
    }
  }, [schema]);

  const handleSubmit = async (data: any) => {
    if (!validateJson(data)) {
      setError('Invalid form data');
      return;
    }

    try {
      console.log("Submitting data:", data);
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
    console.error('Error parsing schema:', e);
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
        <MuiDialogTitle>Edit Schedule for {destination}</MuiDialogTitle>
        
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
              schema={schemaObj}
              formData={formData}
              validator={validator}
              onChange={({ formData: newData }) => {
                console.log("Form data changed:", newData);
                setFormData(newData);
                // Update raw text whenever form data changes
                setRawText(JSON.stringify(newData, null, 2));
              }}
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
                onChange={(e) => setRawText(e.target.value)}
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
    </ThemeProvider>
  );
}; 