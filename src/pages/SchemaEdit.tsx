import React, { useState, useEffect } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { AutoForm, AutoFields } from 'uniforms-mui';
import { JSONSchemaBridge } from 'uniforms-bridge-json-schema';
import Ajv from 'ajv';
import { Box, Button, Paper, Tabs, Tab, Typography } from '@mui/material';
import Form from '@rjsf/mui';
import validator from '@rjsf/validator-ajv8';

interface EditorState {
  schema: any;
  initialData: any;
  returnUrl: string;
  saveEndpoint: string;
  saveMethod: string;
}

const SchemaEdit: React.FC = () => {
  const [state, setState] = useState<EditorState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState(0);
  const [rawJson, setRawJson] = useState('');
  const [rawJsonValid, setRawJsonValid] = useState(true);
  const location = useLocation();
  const navigate = useNavigate();

  useEffect(() => {
    // Get state directly from the router location state
    const routerState = location.state as EditorState | null;
    
    if (!routerState) {
      // Fall back to URL parameters for backward compatibility
      const params = new URLSearchParams(location.search);
      const stateParam = params.get('state');
      
      if (stateParam) {
        try {
          const decodedState = JSON.parse(atob(stateParam));
          setState(decodedState);
          setRawJson(JSON.stringify(decodedState.initialData || {}, null, 2));
          setLoading(false);
          return;
        } catch (e) {
          setError('Failed to parse state from URL parameter');
        }
      } else {
        setError('No state provided');
      }
    } else {
      // Use the state from router
      setState(routerState);
      setRawJson(JSON.stringify(routerState.initialData || {}, null, 2));
    }
    
    setLoading(false);
  }, [location]);

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}>
        <Typography>Loading...</Typography>
      </Box>
    );
  }

  if (error) {
    return (
      <Box sx={{ p: 4 }}>
        <Typography color="error" variant="h6">Error</Typography>
        <Typography color="error">{error}</Typography>
      </Box>
    );
  }

  if (!state) {
    return (
      <Box sx={{ p: 4 }}>
        <Typography color="error" variant="h6">Error</Typography>
        <Typography color="error">No state provided</Typography>
      </Box>
    );
  }

  const ajv = new Ajv({ 
    allErrors: true, 
    useDefaults: true,
    strict: false // Turn off strict mode to allow defaults
  });
  const schema = state.schema;
  const validator = ajv.compile(schema);
  const bridge = new JSONSchemaBridge(schema, validator);

  const handleRawJsonChange = (event: React.ChangeEvent<HTMLTextAreaElement>) => {
    const newValue = event.target.value;
    setRawJson(newValue);
    
    try {
      JSON.parse(newValue);
      setRawJsonValid(true);
    } catch (error) {
      setRawJsonValid(false);
    }
  };

  const handleSubmit = async (data: any) => {
    try {
      const response = await fetch(state.saveEndpoint, {
        method: state.saveMethod,
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(data),
      });

      if (!response.ok) {
        throw new Error('Failed to save');
      }

      navigate(state.returnUrl);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save');
    }
  };

  const handleSave = async () => {
    try {
      const data = JSON.parse(rawJson);
      await handleSubmit(data);
    } catch (e) {
      setError('Invalid JSON format');
    }
  };

  const handleCancel = () => {
    navigate(state.returnUrl);
  };

  return (
    <Box sx={{ p: 4, maxWidth: 800, margin: '0 auto' }}>
      <Paper sx={{ p: 3, mb: 2 }}>
        <Tabs value={tab} onChange={(_, newValue) => setTab(newValue)}>
          <Tab label="Editor" />
          <Tab label="Raw" />
        </Tabs>

        {tab === 0 && (
          <AutoForm
            schema={bridge}
            model={state.initialData}
            validator={validator}
            onChange={(data) => setState({ ...state, initialData: data })}
            onSubmit={(data) => handleSubmit(data)}
            liveValidate
          >
            <AutoFields />
            <Box sx={{ mt: 2, display: 'flex', justifyContent: 'flex-end', gap: 2 }}>
              <Button variant="outlined" onClick={handleCancel}>Cancel</Button>
              <Button type="submit" variant="contained">Save</Button>
            </Box>
          </AutoForm>
        )}

        {tab === 1 && (
          <Box sx={{ mt: 2 }}>
            <textarea
              value={rawJson}
              onChange={handleRawJsonChange}
              style={{
                width: '100%',
                height: '400px',
                fontFamily: 'monospace',
                padding: '10px',
                border: rawJsonValid ? '1px solid #ccc' : '1px solid red',
                borderRadius: '4px',
                marginBottom: '20px'
              }}
            />
            <Box sx={{ display: 'flex', justifyContent: 'flex-end', gap: 2 }}>
              <Button variant="outlined" onClick={handleCancel}>Cancel</Button>
              <Button 
                variant="contained" 
                onClick={handleSave}
                disabled={!rawJsonValid}
              >
                Save
              </Button>
            </Box>
          </Box>
        )}
      </Paper>
    </Box>
  );
};

export default SchemaEdit; 