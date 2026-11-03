import { array, boolean, date, integer, nullable, object, oneOf, text } from './contracts'
export const supportAddress = 'waelounie@gmail.com'
export const supportTypes = ['Problème technique', "Question sur l’application", "Suggestion d’amélioration", 'Autre'] as const
export const contactCategories = { access: "Demande d’accès", catalogue: 'Problème de catalogue', order: 'Question sur une commande', stock: 'Problème de stock', other: 'Autre' }
export function supportText(type: string, subject: string, description: string) {
  if (!(supportTypes as readonly string[]).includes(type) || !subject.trim() || subject.length > 100 || !description.trim() || description.length > 800
      || /[\r\n\x00-\x1f\x7f]/.test(subject) || /[\x00-\x08\x0b-\x1f\x7f]/.test(description)) {
    throw new Error('Renseignez un objet (100 caractères maximum) et une description (800 caractères maximum).')
  }
  return { subject: '[StockPilot AI] ' + type + ' — ' + subject, body: description }
}
export function supportMailto(type: string, subject: string, description: string) {
  const message = supportText(type, subject, description)
  const url = 'mailto:' + supportAddress + '?subject=' + encodeURIComponent(message.subject) + '&body=' + encodeURIComponent(message.body)
  if (url.length > 2000) throw new Error('Ce message est trop long pour un lien email. Utilisez Copier le message.')
  return url
}
export const contactDecoder = object({
  request_id: text, subject: text, category: oneOf('access', 'catalogue', 'order', 'stock', 'other'),
  description: text, created_by: text, created_at: date, updated_at: date, status: oneOf('open', 'resolved'), revision: integer,
})
export const contactReply = object({ reply_id: text, request_id: text, body: text, created_by: text, created_at: date })
export const contactPage = object({ items: array(contactDecoder), total: integer, has_more: boolean, limit: integer, offset: integer })
export const replyPage = object({ items: array(contactReply), total: integer, has_more: boolean, limit: integer, offset: integer })
export const contactReceipt = object({ operation_id: text, request_id: text, reply_id: nullable(text) })
export type Contact = ReturnType<typeof contactDecoder>
