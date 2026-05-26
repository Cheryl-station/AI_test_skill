function buildUserPayload(name, age) {
  if (!name) {
    throw new Error('Name is required');
  }
  if (age < 0) {
    throw new Error('Age must be a positive number');
  }
  return { name, age };
}

async function createUser(baseUrl, user) {
  const response = await fetch(`${baseUrl}/api/users`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(user),
  });

  if (!response.ok) {
    throw new Error(`API request failed with status ${response.status}`);
  }

  return response.json();
}

module.exports = { buildUserPayload, createUser };