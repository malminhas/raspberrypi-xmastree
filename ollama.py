#!/usr/bin/env python3
"""
ollama.py
=========

Ollama API client module for interacting with local Ollama LLM instances.
Provides functions for listing models, setting the active model, and performing
inference operations.

This module is designed to work with Ollama's local API and provides the same
interface as the greenpt module for easy swapping.
"""

import os
import random
from pathlib import Path
from typing import Optional, List, Dict, Any

import requests  # type: ignore

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

# Ollama API configuration - can be overridden via environment variables
API_BASE_URL = os.environ.get("OLLAMA_API_BASE_URL", "http://localhost:11434")
DEFAULT_MODEL_ID = os.environ.get("OLLAMA_MODEL_ID", "llama3.2:3b")

# Path to file storing the selected model (for persistence across sessions)
MODEL_STORAGE_FILE = Path(__file__).parent / "selected_ollama_model.txt"

# Current active model ID (in-memory, can be changed via set_model)
_current_model_id = None  # Will be loaded from file or default


# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------

def _handle_response(resp: requests.Response) -> Dict[str, Any]:
    """
    Raise on error, otherwise return parsed JSON.
    Provides helpful error messages for API failures.
    """
    try:
        resp.raise_for_status()
    except requests.HTTPError as exc:
        # Provide a helpful error message
        error_body = resp.text if resp.text else "(no body)"
        raise RuntimeError(
            f"API request failed [{resp.status_code}]: {error_body}"
        ) from exc

    # Try to parse JSON, handle empty or invalid responses
    try:
        return resp.json()
    except (ValueError, requests.exceptions.JSONDecodeError) as json_exc:
        # If response is not valid JSON, provide helpful error
        raise RuntimeError(
            f"API returned invalid JSON [{resp.status_code}]: {resp.text[:200] if resp.text else '(empty response)'}"
        ) from json_exc


def _get_headers() -> Dict[str, str]:
    """Get common headers for every request."""
    return {
        "Content-Type": "application/json",
    }


# -----------------------------------------------------------------------------
# Model Management
# -----------------------------------------------------------------------------

def list_models() -> Optional[List[Dict[str, Any]]]:
    """
    List all available models from the Ollama API.

    Returns a list of dictionaries, each describing a model.
    Example entry:
    {
        "name": "llama3.2:3b",
        "model": "llama3.2:3b",
        "modified_at": "2024-01-01T00:00:00Z",
        "size": 2000000000,
        "digest": "...",
        "details": {...}
    }

    Returns:
        A list of model dictionaries, each containing model information.
        Returns None if the API call fails or if Ollama is not running.
    """
    try:
        url = f"{API_BASE_URL}/api/tags"
        response = requests.get(url, headers=_get_headers(), timeout=10)
        data = _handle_response(response)
        # Extract models list - Ollama API format uses "models" key
        models = data.get("models", [])
        return models

    except requests.exceptions.ConnectionError:
        print("Ollama API connection failed - is Ollama running? Start with 'ollama serve'")
        return None
    except requests.exceptions.Timeout:
        print("Ollama API request timed out")
        return None
    except RuntimeError as exc:
        print(f"Ollama API request failed: {exc}")
        return None
    except requests.exceptions.RequestException as exc:
        print(f"Ollama API request failed: {exc}")
        return None
    except Exception as exc:
        print(f"Unexpected error calling Ollama API: {exc}")
        return None


def get_model() -> str:
    """
    Get the currently active model ID.
    Loads from file if available, otherwise uses the default.

    Returns:
        The current model ID string.
    """
    global _current_model_id

    # Return in-memory value if set
    if _current_model_id is not None:
        return _current_model_id

    # Try to load from file
    try:
        if MODEL_STORAGE_FILE.exists():
            with open(MODEL_STORAGE_FILE, "r", encoding="utf-8") as fp:
                model_id = fp.read().strip()
                if model_id:
                    _current_model_id = model_id
                    return model_id
    except Exception as exc:
        print(f"Warning: Could not read model from file: {exc}")

    # Fall back to default
    _current_model_id = DEFAULT_MODEL_ID
    return _current_model_id


def set_model(model_id: str) -> bool:
    """
    Set the active model ID to use for inference operations.
    Stores the chosen model ID in a file for persistence across sessions.

    Args:
        model_id: The model ID to set (e.g., "llama3.2:3b", "mistral:latest", etc.)

    Returns:
        True if the model was set successfully, False otherwise.
    """
    global _current_model_id

    if not model_id:
        print("Model ID cannot be empty")
        return False

    # Store in memory
    _current_model_id = model_id
    print(f"Setting active model to: {model_id}")

    # Persist to file
    try:
        with open(MODEL_STORAGE_FILE, "w", encoding="utf-8") as fp:
            fp.write(model_id)
    except Exception as exc:
        print(f"Warning: Could not save model to file: {exc}")
        # Still return True since in-memory setting succeeded

    return True


# -----------------------------------------------------------------------------
# Inference
# -----------------------------------------------------------------------------

def infer(prompt: str, max_tokens: int = 150, temperature: float = 0.7,
          model_id: Optional[str] = None, **kwargs) -> Optional[str]:
    """
    Perform inference using the Ollama API with the given prompt.
    Sends a completion request to the currently selected model (or the specified model).
    Additional keyword arguments are passed straight to the API.

    Args:
        prompt: The text prompt to send to the model.
        max_tokens: Maximum number of tokens to generate (default: 150).
        temperature: Sampling temperature (default: 0.7).
        model_id: Optional model ID to use. If None, uses the currently set model.
        **kwargs: Additional parameters to pass to the API (e.g., top_p, top_k, etc.)

    Returns:
        The generated text response, or None if the API call fails.
    """
    # Use provided model_id or fall back to current model
    model = model_id if model_id is not None else get_model()
    print(f"Using model for inference: {model}")

    try:
        url = f"{API_BASE_URL}/api/chat"

        # Build options dict with temperature and max tokens
        options = {
            "temperature": temperature,
            "num_predict": max_tokens,
        }
        # Merge any additional kwargs into options
        options.update(kwargs)

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,  # Get complete response
            "options": options,
        }

        response = requests.post(url, headers=_get_headers(), json=payload, timeout=60)
        result = _handle_response(response)

        # Extract the generated text from the response
        # Ollama chat API returns: {"message": {"role": "assistant", "content": "..."}}
        content = result.get("message", {}).get("content", "")
        return content.strip() if content else None

    except requests.exceptions.ConnectionError:
        print("Ollama API connection failed - is Ollama running? Start with 'ollama serve'")
        return None
    except requests.exceptions.Timeout:
        print("Ollama API request timed out")
        return None
    except RuntimeError as exc:
        print(f"Ollama API request failed: {exc}")
        return None
    except requests.exceptions.RequestException as exc:
        print(f"Ollama API request failed: {exc}")
        return None
    except Exception as exc:
        print(f"Unexpected error calling Ollama API: {exc}")
        return None


# -----------------------------------------------------------------------------
# Convenience Functions
# -----------------------------------------------------------------------------

def get_joke(previous_jokes: Optional[List[str]] = None) -> Optional[str]:
    """Request a family-friendly joke from the Ollama API.

    Args:
        previous_jokes: Optional list of previously told jokes to avoid repetition

    Returns:
        The joke text or None if the request fails.

    Uses randomized prompts and higher temperature for variety. If previous_jokes
    is provided, the prompt will explicitly request a different joke from those.
    """
    # Vary the prompt to encourage different types of jokes
    joke_types = [
        "a pun",
        "a one-liner",
        "a knock-knock joke",
        "a wordplay joke",
        "a dad joke",
        "a clever joke",
        "a silly joke",
        "a witty joke"
    ]

    topics = [
        "Christmas",
        "the holidays",
        "winter",
        "Santa",
        "reindeer",
        "snow",
        "gifts",
        "family gatherings"
    ]

    # Randomly select joke type and topic for variety
    joke_type = random.choice(joke_types)
    topic = random.choice(topics)

    # Build base prompt with subversive anti-American angle
    base_prompt = (f"Share a new, subversive {joke_type} about {topic} with an anti-American angle. "
                   f"The joke should subtly critique or satirize American culture, politics, or society. "
                   f"Maximum 50 words. Return only the joke text. No emojis or non-alphabetic characters. "
                   f"Be creative, original, and witty in your critique.")

    # Add context about previous jokes if provided
    if previous_jokes and len(previous_jokes) > 0:
        previous_jokes_text = "\n".join([f"- {joke}" for joke in previous_jokes])
        base_prompt += f"\n\nDo NOT repeat any of these jokes:\n{previous_jokes_text}\n\nMake sure your joke is completely different from all of the above."

    prompt = base_prompt

    # Use higher temperature (0.9-1.0) for more variety and creativity
    temperature = random.uniform(0.85, 1.0)

    print(f"[Ollama] Prompt: {prompt}")
    print(f"[Ollama] Temperature: {temperature:.2f}")
    # 150 tokens is sufficient for ~50 words (allows buffer for longer jokes)
    result = infer(prompt, max_tokens=150, temperature=temperature)
    if result:
        print(f"[Ollama] Response: {result}")
    return result


def get_flattery(previous_flattery: Optional[List[str]] = None) -> Optional[str]:
    """Request over-the-top ostentatiously sycophantic praise from the Ollama API.

    Args:
        previous_flattery: Optional list of previously given flattery to avoid repetition

    Returns:
        The flattery text or None if the request fails.

    If previous_flattery is provided, the prompt will explicitly request different
    flattery from those previously given.
    """
    # Build base prompt
    prompt = ("Write a humorous, absurdly over-the-top piece of sycophantic effusive praise for me. "
              "Make it ridiculously flattering. Under 50 words. No emojis or non-alphabetic characters.")

    # Add context about previous flattery if provided
    if previous_flattery and len(previous_flattery) > 0:
        previous_flattery_text = "\n".join([f"- {flattery}" for flattery in previous_flattery])
        prompt += f"\n\nDo NOT repeat any of this flattery:\n{previous_flattery_text}\n\nMake sure your flattery is completely different from all of the above."

    print(f"[Ollama] Prompt: {prompt}")
    # 150 tokens is sufficient for ~50 words (allows buffer for longer flattery)
    result = infer(prompt, max_tokens=150)
    if result:
        print(f"[Ollama] Response: {result}")
    return result


# -----------------------------------------------------------------------------
# Integrated Tests
# -----------------------------------------------------------------------------

def test_list_models() -> bool:
    """
    Test function to verify that we can list models from the API.

    Returns:
        True if the test passes, False otherwise.
    """
    print("Testing list_models()...")
    models = list_models()

    if models is None:
        print("  ❌ FAILED: list_models() returned None")
        return False

    if not isinstance(models, list):
        print(f"  ❌ FAILED: Expected list, got {type(models)}")
        return False

    print(f"  ✅ PASSED: Retrieved {len(models)} model(s)")
    if models:
        print(f"  Available models: {[m.get('name', m.get('model', 'unknown')) for m in models]}")
    return True


def test_set_model() -> bool:
    """
    Test function to verify that we can set the active model.

    Returns:
        True if the test passes, False otherwise.
    """
    print("Testing set_model()...")

    # Save current model and file state
    original_model = get_model()
    file_existed = MODEL_STORAGE_FILE.exists()
    original_file_content = None
    if file_existed:
        try:
            original_file_content = MODEL_STORAGE_FILE.read_text(encoding="utf-8")
        except Exception:
            pass

    try:
        # Test setting a valid Ollama model
        test_model = "gemma2:2b"
        if not set_model(test_model):
            print(f"  ❌ FAILED: set_model('{test_model}') returned False")
            return False
        
        if get_model() != test_model:
            print(f"  ❌ FAILED: Model not set correctly. Expected '{test_model}', got '{get_model()}'")
            return False
        
        # Verify file was written
        if not MODEL_STORAGE_FILE.exists():
            print(f"  ❌ FAILED: Model file was not created")
            return False
        
        file_content = MODEL_STORAGE_FILE.read_text(encoding="utf-8").strip()
        if file_content != test_model:
            print(f"  ❌ FAILED: Model file content incorrect. Expected '{test_model}', got '{file_content}'")
            return False
        
        # Test setting another Ollama model
        test_model2 = "gemma2:2b"
        if not set_model(test_model2):
            print(f"  ❌ FAILED: set_model('{test_model2}') returned False")
            return False
        
        if get_model() != test_model2:
            print(f"  ❌ FAILED: Model not set correctly. Expected '{test_model2}', got '{get_model()}'")
            return False

        print(f"  ✅ PASSED: Successfully set and retrieved models (with file persistence)")
        return True
    finally:
        # Restore original model and file state
        if original_file_content is not None:
            try:
                MODEL_STORAGE_FILE.write_text(original_file_content, encoding="utf-8")
            except Exception:
                pass
        elif file_existed:
            # File didn't exist before, but we created it - restore by deleting
            try:
                MODEL_STORAGE_FILE.unlink()
            except Exception:
                pass
        else:
            # Restore the original model
            set_model(original_model)

        # Reset in-memory cache
        global _current_model_id
        _current_model_id = None


def test_inference() -> bool:
    """
    Test function to verify that we can perform inference.

    Returns:
        True if the test passes, False otherwise.
    """
    print("Testing infer()...")

    test_prompt = "Say 'Hello, world!' and nothing else."
    result = infer(test_prompt, max_tokens=20)

    if result is None:
        print("  ❌ FAILED: infer() returned None")
        return False

    if not isinstance(result, str):
        print(f"  ❌ FAILED: Expected string, got {type(result)}")
        return False

    if len(result) == 0:
        print("  ❌ FAILED: Inference returned empty string")
        return False

    print(f"  ✅ PASSED: Inference successful. Response: '{result[:50]}...'")
    return True


def test_inference_with_model_id() -> bool:
    """
    Test function to verify that we can perform inference with a specific model ID.

    Returns:
        True if the test passes, False otherwise.
    """
    print("Testing infer() with explicit model_id...")

    # Save current model
    original_model = get_model()

    # Test inference with explicit model ID
    # Use gemma2:2b model (used on Raspberry Pi)
    test_model = "gemma2:2b"
    test_prompt = "Say 'Test' and nothing else."
    result = infer(test_prompt, max_tokens=10, model_id=test_model)

    if result is None:
        print("  ❌ FAILED: infer() with model_id returned None")
        set_model(original_model)
        return False

    # Verify that the current model wasn't changed
    if get_model() != original_model:
        print(f"  ❌ FAILED: Explicit model_id changed the active model")
        set_model(original_model)
        return False

    print(f"  ✅ PASSED: Inference with explicit model_id successful")
    return True


def run_all_tests() -> bool:
    """
    Run all integrated tests and report results.

    Returns:
        True if all tests pass, False otherwise.
    """
    print("=" * 60)
    print("Running Ollama API integrated tests")
    print("=" * 60)
    print()

    # Check if Ollama is running
    try:
        response = requests.get(f"{API_BASE_URL}/api/tags", timeout=2)
        if response.status_code != 200:
            print("⚠️  WARNING: Ollama API is not responding correctly.")
            print("   Make sure Ollama is running with: ollama serve")
            print()
    except requests.exceptions.ConnectionError:
        print("⚠️  WARNING: Cannot connect to Ollama API.")
        print("   Make sure Ollama is running with: ollama serve")
        print()
    except Exception:
        pass

    tests = [
        ("List Models", test_list_models),
        ("Set Model", test_set_model),
        ("Inference", test_inference),
        ("Inference with Model ID", test_inference_with_model_id),
    ]

    results = []
    for test_name, test_func in tests:
        print(f"Test: {test_name}")
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"  ❌ FAILED: Exception raised: {e}")
            results.append((test_name, False))
        print()

    print("=" * 60)
    print("Test Results Summary")
    print("=" * 60)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for test_name, result in results:
        status = "✅ PASSED" if result else "❌ FAILED"
        print(f"  {status}: {test_name}")

    print()
    print(f"Total: {passed}/{total} tests passed")
    print("=" * 60)

    return passed == total


if __name__ == "__main__":
    # Run tests when executed directly
    success = run_all_tests()
    exit(0 if success else 1)
