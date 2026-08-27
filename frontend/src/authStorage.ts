const TOKEN_KEY = 'talent_graph_session'

export async function readSessionToken(): Promise<string | null> {
  return sessionStorage.getItem(TOKEN_KEY)
}

export async function writeSessionToken(token: string): Promise<void> {
  sessionStorage.setItem(TOKEN_KEY, token)
}

export async function clearSessionToken(): Promise<void> {
  sessionStorage.removeItem(TOKEN_KEY)
}
