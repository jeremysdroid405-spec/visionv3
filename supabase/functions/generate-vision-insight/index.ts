// Supabase Edge Function: generate-vision-insight
// Purpose: AI "Oracle" that generates badass sports betting insights using Claude Sonnet 4.5
// Path: supabase/functions/generate-vision-insight/index.ts

import { serve } from "https://deno.land/std@0.168.0/http/server.ts"
import { createClient } from 'https://esm.sh/@supabase/supabase-js@2'

const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
}

interface PlayerInsightRequest {
  player_id: string
  name: string
  current_line: number
  l10_rate: number
  pace_factor: number
  fatigue: string // "Fresh", "Normal", "Fatigued"
  usage: number // Usage bump percentage
  risk_level: string // "Low", "Med", "High"
  stat_type: string
  is_demon: boolean
  is_goblin: boolean
  projected_score?: number // AI's projected score for discrepancy check
}

serve(async (req: Request) => {
  // Handle CORS preflight
  if (req.method === 'OPTIONS') {
    return new Response('ok', { headers: corsHeaders })
  }

  try {
    const payload: PlayerInsightRequest = await req.json()
    const { 
      player_id, 
      name, 
      current_line, 
      l10_rate, 
      pace_factor, 
      fatigue, 
      usage, 
      risk_level,
      stat_type,
      is_demon,
      is_goblin,
      projected_score
    } = payload

    // Validate required fields
    if (!player_id || !name) {
      throw new Error('Missing required fields: player_id, name')
    }

    // 1. Initialize Supabase client with service role for admin access
    const supabaseUrl = Deno.env.get('SUPABASE_URL')
    const supabaseServiceKey = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')
    
    if (!supabaseUrl || !supabaseServiceKey) {
      throw new Error('Missing Supabase configuration')
    }

    const supabase = createClient(supabaseUrl, supabaseServiceKey)

    // 2. Build the context-aware prompt
    const playerType = is_demon ? "DEMON (High Payout)" : is_goblin ? "GOBLIN (High Safety)" : "Standard"
    
    // Calculate discrepancy if projected score provided
    let discrepancyNote = ""
    if (projected_score && current_line > 0) {
      const discrepancyPercent = Math.abs((projected_score - current_line) / current_line) * 100
      if (discrepancyPercent > 15) {
        const direction = projected_score > current_line ? "OVER" : "UNDER"
        discrepancyNote = `\nCRITICAL EDGE DETECTED: Model projects ${projected_score.toFixed(1)} vs line ${current_line}. ${discrepancyPercent.toFixed(0)}% discrepancy favors ${direction}. Mention this edge explicitly.`
      }
    }

    const systemPrompt = `You are a sharp, aggressive sports betting analyst for an elite 2026 app called "Demon & Goblin." 
Analyze the following data and provide a 1-sentence "badass" insight. 
Do not use filler words. Focus on why the "future" favors this bet. 
Use a punchy, high-tech tone. Be prophetic and confident.
Never mention uncertainty. Speak as if you've seen the future.`

    const userPrompt = `
PLAYER: ${name}
PROP TYPE: ${stat_type}
CLASSIFICATION: ${playerType}
LINE: ${current_line}
L10 HIT RATE: ${l10_rate}%
ADVANCED ANALYTICS:
- Pace Adjustment: ${pace_factor > 1 ? '+' : ''}${((pace_factor - 1) * 100).toFixed(0)}%
- Fatigue Status: ${fatigue}
- Usage Bump: ${usage > 0 ? '+' : ''}${usage.toFixed(0)}%
- Volatility Risk: ${risk_level}
${discrepancyNote}

Generate a 1-sentence badass insight for a pro bettor. Why does the data favor this outcome?
Tone: Aggressive, sharp, prophetic. No fluff. Maximum 25 words.`

    // 3. Call Claude Sonnet 4.5 via Anthropic API
    const anthropicKey = Deno.env.get('ANTHROPIC_API_KEY')
    
    if (!anthropicKey) {
      throw new Error('Missing ANTHROPIC_API_KEY in secrets')
    }

    const aiResponse = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'x-api-key': anthropicKey,
        'Content-Type': 'application/json',
        'anthropic-version': '2023-06-01'
      },
      body: JSON.stringify({
        model: 'claude-sonnet-4-5-20250929',
        max_tokens: 150,
        system: systemPrompt,
        messages: [{ role: 'user', content: userPrompt }]
      })
    })

    if (!aiResponse.ok) {
      const errorText = await aiResponse.text()
      throw new Error(`Claude API error: ${aiResponse.status} - ${errorText}`)
    }

    const aiData = await aiResponse.json()
    
    if (!aiData.content || !aiData.content[0] || !aiData.content[0].text) {
      throw new Error('Invalid response from Claude API')
    }

    const insight = aiData.content[0].text.trim()

    // 4. Update the daily_insights table in Supabase
    // First try to update existing record, if not exists, insert new one
    const { data: existingRecord, error: selectError } = await supabase
      .from('daily_insights')
      .select('id')
      .eq('player_id', player_id)
      .single()

    if (existingRecord) {
      // Update existing record
      const { error: updateError } = await supabase
        .from('daily_insights')
        .update({ 
          insight_summary: insight,
          ai_generated_at: new Date().toISOString(),
          ai_model: 'claude-sonnet-4.5'
        })
        .eq('player_id', player_id)

      if (updateError) throw updateError
    } else {
      // Insert new record if doesn't exist
      const { error: insertError } = await supabase
        .from('daily_insights')
        .insert({
          player_id: player_id,
          player_name: name,
          insight_summary: insight,
          ai_generated_at: new Date().toISOString(),
          ai_model: 'claude-sonnet-4.5'
        })

      if (insertError && !insertError.message.includes('duplicate')) {
        throw insertError
      }
    }

    // 5. Return success response
    return new Response(
      JSON.stringify({ 
        success: true, 
        player: name,
        insight: insight,
        classification: playerType,
        generated_at: new Date().toISOString()
      }), 
      {
        headers: { ...corsHeaders, 'Content-Type': 'application/json' },
        status: 200,
      }
    )

  } catch (error) {
    console.error('Vision Insight Error:', error)
    return new Response(
      JSON.stringify({ 
        success: false, 
        error: error.message || 'Unknown error occurred'
      }), 
      {
        headers: { ...corsHeaders, 'Content-Type': 'application/json' },
        status: 400,
      }
    )
  }
})
