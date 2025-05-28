"""
Test the new generate instruction publish parameter and output_var functionality
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime
from routes.scheduler_handlers import handle_generate, handle_publish
from routes.scheduler_utils import default_context


@pytest.fixture
def mock_generation_service():
    """Mock the generation service to return predictable results"""
    mock_service = Mock()
    mock_service.return_value = [
        {
            "message": "http://example.com/generated1.jpg",
            "published_path": "/output/test_bucket/generated1.jpg",
            "success": True
        }
    ]
    return mock_service


@pytest.fixture
def mock_publish_to_destination():
    """Mock the publish_to_destination function"""
    with patch('routes.scheduler_handlers.publish_to_destination') as mock:
        mock.return_value = {
            "success": True,
            "meta": {
                "filename": "20250101-120000-abcd1234.jpg"
            }
        }
        yield mock


@pytest.fixture
def test_context():
    """Create a test context"""
    context = default_context()
    context["vars"] = {}
    return context


def test_generate_with_publish_false(mock_generation_service, mock_publish_to_destination, test_context):
    """Test generate instruction with publish=False saves to bucket but doesn't display"""
    
    # Mock the generation service
    with patch('routes.scheduler_handlers.get_generation_service', return_value=mock_generation_service):
        with patch('routes.bucketer.bucket_path') as mock_bucket_path:
            mock_bucket_path.return_value.joinpath = Mock(return_value="/output/test_dest/20250101-120000-abcd1234.jpg")
            
            instruction = {
                "action": "generate",
                "input": {"prompt": "test prompt"},
                "publish": False,
                "output_var": "test_img"
            }
            
            output = []
            now = datetime.now()
            
            # Run the handler
            handle_generate(instruction, test_context, now, output, "test_dest")
            
            # Verify generation was called with empty targets
            mock_generation_service.assert_called_once()
            call_args = mock_generation_service.call_args[0][0]
            assert call_args["data"]["targets"] == []
            
            # Verify publish_to_destination was called with silent=True
            mock_publish_to_destination.assert_called_once()
            assert mock_publish_to_destination.call_args[1]["silent"] == True
            assert mock_publish_to_destination.call_args[1]["publish_destination_id"] == "test_dest"
            
            # Verify the output variable was set
            assert "test_img" in test_context["vars"]
            assert test_context["vars"]["test_img"] == "/output/test_dest/20250101-120000-abcd1234.jpg"


def test_generate_with_publish_true(mock_generation_service, test_context):
    """Test generate instruction with publish=True (default) displays normally"""
    
    # Mock the generation service
    with patch('routes.scheduler_handlers.get_generation_service', return_value=mock_generation_service):
        instruction = {
            "action": "generate",
            "input": {"prompt": "test prompt"},
            # publish defaults to True
            "output_var": "test_img"
        }
        
        output = []
        now = datetime.now()
        
        # Run the handler
        handle_generate(instruction, test_context, now, output, "test_dest")
        
        # Verify generation was called with the destination in targets
        mock_generation_service.assert_called_once()
        call_args = mock_generation_service.call_args[0][0]
        assert call_args["data"]["targets"] == ["test_dest"]
        
        # Verify the output variable was set
        assert "test_img" in test_context["vars"]
        assert test_context["vars"]["test_img"] == "/output/test_bucket/generated1.jpg"


def test_generate_batch_with_output_vars(test_context):
    """Test generate instruction with batch and output_vars"""
    
    # Mock service to return multiple results
    mock_service = Mock()
    mock_service.return_value = [
        {"message": "http://example.com/img1.jpg", "published_path": "/output/bucket/img1.jpg"},
        {"message": "http://example.com/img2.jpg", "published_path": "/output/bucket/img2.jpg"},
        {"message": "http://example.com/img3.jpg", "published_path": "/output/bucket/img3.jpg"}
    ]
    
    with patch('routes.scheduler_handlers.get_generation_service', return_value=mock_service):
        instruction = {
            "action": "generate",
            "input": {"prompt": "test prompt"},
            "output_var": "batch_imgs"
        }
        
        output = []
        now = datetime.now()
        
        # Run the handler
        handle_generate(instruction, test_context, now, output, "test_dest")
        
        # Verify the output variable was set with a list
        assert "batch_imgs" in test_context["vars"]
        assert isinstance(test_context["vars"]["batch_imgs"], list)
        assert len(test_context["vars"]["batch_imgs"]) == 3
        assert test_context["vars"]["batch_imgs"][0] == "/output/bucket/img1.jpg"
        assert test_context["vars"]["batch_imgs"][1] == "/output/bucket/img2.jpg"
        assert test_context["vars"]["batch_imgs"][2] == "/output/bucket/img3.jpg"


def test_publish_instruction_with_var(mock_publish_to_destination, test_context):
    """Test publish instruction using a variable containing images"""
    
    # Set up context with image list in variable
    test_context["vars"]["batch_imgs"] = [
        "/output/bucket/img1.jpg",
        "/output/bucket/img2.jpg"
    ]
    
    instruction = {
        "action": "publish",
        "var": "batch_imgs",
        # targets defaults to current destination
        "silent": True
    }
    
    output = []
    now = datetime.now()
    
    # Run the handler
    handle_publish(instruction, test_context, now, output, "test_dest")
    
    # Verify publish_to_destination was called for each image
    assert mock_publish_to_destination.call_count == 2
    
    # Check that silent=True was passed and target defaulted to test_dest
    for call in mock_publish_to_destination.call_args_list:
        assert call[1]["silent"] == True
        assert call[1]["publish_destination_id"] == "test_dest"


def test_publish_instruction_with_single_image_var(mock_publish_to_destination, test_context):
    """Test publish instruction with a variable containing a single image path"""
    
    # Set up context with single image in variable
    test_context["vars"]["selected_img"] = "/output/bucket/best.jpg"
    
    instruction = {
        "action": "publish",
        "var": "selected_img"
    }
    
    output = []
    now = datetime.now()
    
    # Run the handler
    handle_publish(instruction, test_context, now, output, "test_dest")
    
    # Verify publish_to_destination was called once
    assert mock_publish_to_destination.call_count == 1
    assert mock_publish_to_destination.call_args[0][0] == "/output/bucket/best.jpg" 